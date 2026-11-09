package com.doccompare;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.springframework.dao.DataAccessException;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.server.ResponseStatusException;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.ObjectMapper;

@Service
class ChatMemory {
    private static final String TURN_TABLES = "FROM chat_turn t LEFT JOIN chat_turn_model m ON m.turn_id = t.id "
            + "LEFT JOIN chat_turn_details d ON d.turn_id = t.id ";
    private static final String TURNS = "SELECT t.id, t.question, t.answer, t.created_at, m.model, d.reasoning " + TURN_TABLES;
    private static final String MODEL_SCOPE = "WHERE t.owner_id = :owner AND CAST(m.model AS BINARY) = CAST(:model AS BINARY) ";
    // Older turns remain visible, but were generated with mixed-model context.
    private static final String MEMORY_SCOPE = MODEL_SCOPE + "AND d.turn_id IS NOT NULL ";
    private static final int HISTORY_PAGE = 30;
    private static final int RECENT_TURNS = 8;
    private static final Duration MEMORY_TTL = Duration.ofMinutes(30);

    private final JdbcClient jdbc;
    private final StringRedisTemplate redis;
    private final ObjectMapper json;
    private final TransactionTemplate transactions;
    private final DocumentAgent agent;

    ChatMemory(JdbcClient jdbc, StringRedisTemplate redis, ObjectMapper json,
               TransactionTemplate transactions, DocumentAgent agent) {
        this.jdbc = jdbc;
        this.redis = redis;
        this.json = json;
        this.transactions = transactions;
        this.agent = agent;
    }

    Page history(long owner, String model, Long before) {
        List<Turn> newest = jdbc.sql(TURNS + MODEL_SCOPE + "AND t.id < :before ORDER BY t.id DESC LIMIT :limit")
                .param("owner", owner).param("model", resolveModel(model))
                .param("before", before == null ? Long.MAX_VALUE : before)
                .param("limit", HISTORY_PAGE + 1).query(ChatMemory::turn).list();
        boolean more = newest.size() > HISTORY_PAGE;
        List<Turn> page = newest.subList(0, Math.min(newest.size(), HISTORY_PAGE));
        return new Page(page.reversed(), more ? page.getLast().id() : null);
    }

    Turn send(long owner, Send request) {
        String message = request.message() == null ? "" : request.message().strip();
        if (request.requestId() == null || message.isEmpty() || message.length() > 2000) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "请输入 1–2,000 字的消息。");
        }
        validateModel(request.model());
        UUID requestId = request.requestId();
        Turn saved = completed(owner, requestId, message, request.model());
        if (saved != null) {
            return saved;
        }
        String model = resolveModel(request.model());
        long head = latestId(owner, model);
        List<RecentTurn> recent = recent(owner, model, head);
        List<Context> recalled = recent.isEmpty() ? List.of() : recall(owner, model, recent.getFirst().id(), message);
        var prompt = new Prompt(message, recent.stream().map(turn -> new Context(turn.question(), turn.answer())).toList(),
                recalled, model);
        DocumentAgent.ChatAnswer answer = agent.chat(prompt);

        saved = transactions.execute(status -> {
            // Serialize commits for an account without holding a database lock during the model call.
            jdbc.sql("SELECT id FROM app_user WHERE id = :owner FOR UPDATE")
                    .param("owner", owner).query(Long.class).single();
            Turn replay = completed(owner, requestId, message, request.model());
            if (replay != null) {
                return replay;
            }
            if (head != latestId(owner, model)) {
                throw new ResponseStatusException(HttpStatus.CONFLICT, "该模型的对话已有一条新回复，请重试这条消息。");
            }
            jdbc.sql("INSERT INTO chat_turn (owner_id, request_id, question, answer) VALUES (:owner, :id, :question, :answer)")
                    .param("owner", owner).param("id", requestId.toString()).param("question", message)
                    .param("answer", answer.answer()).update();
            Turn turn = completed(owner, requestId, message, null);
            jdbc.sql("INSERT INTO chat_turn_model (turn_id, model) VALUES (:turn, :model)")
                    .param("turn", turn.id()).param("model", answer.model()).update();
            jdbc.sql("INSERT INTO chat_turn_details (turn_id, reasoning) VALUES (:turn, :reasoning)")
                    .param("turn", turn.id()).param("reasoning", answer.reasoning()).update();
            return new Turn(turn.id(), turn.question(), turn.answer(), turn.createdAt(), answer.model(), answer.reasoning());
        });
        if (saved.id() > head) {
            cache(owner, model, latest(owner, model, saved.id()));
        }
        return saved;
    }

    private Turn completed(long owner, UUID requestId, String message, String model) {
        Turn saved = jdbc.sql(TURNS + "WHERE t.owner_id = :owner AND t.request_id = :request")
                .param("owner", owner).param("request", requestId.toString()).query(ChatMemory::turn).optional().orElse(null);
        if (saved != null && (!saved.question().equals(message) || (model != null && !model.equals(saved.model())))) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "发送编号已用于其他消息或模型，请重新发送。");
        }
        return saved;
    }

    private String resolveModel(String model) {
        validateModel(model);
        return model == null ? agent.chatModels().defaultModel() : model;
    }

    private static void validateModel(String model) {
        if (model != null && (model.isBlank() || model.length() > 128)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "请选择有效的聊天模型。");
        }
    }

    private long latestId(long owner, String model) {
        return jdbc.sql("SELECT COALESCE(MAX(t.id), 0) " + TURN_TABLES + MEMORY_SCOPE)
                .param("owner", owner).param("model", model).query(Long.class).single();
    }

    private List<RecentTurn> recent(long owner, String model, long head) {
        if (head == 0) {
            return List.of();
        }
        try {
            String stored = redis.opsForValue().get(cacheKey(owner, model));
            if (stored != null) {
                List<RecentTurn> turns = json.readerForListOf(RecentTurn.class).readValue(stored);
                if (!turns.isEmpty() && turns.getLast().id() == head) {
                    return turns;
                }
            }
        } catch (DataAccessException | JacksonException ignored) {
            // The durable record is also the fallback when the short-term cache is unavailable.
        }
        List<RecentTurn> turns = latest(owner, model, head);
        cache(owner, model, turns);
        return turns;
    }

    private List<RecentTurn> latest(long owner, String model, long head) {
        return jdbc.sql("SELECT t.id, t.question, t.answer " + TURN_TABLES + MEMORY_SCOPE
                        + "AND t.id <= :head ORDER BY t.id DESC LIMIT :limit")
                .param("owner", owner).param("model", model).param("head", head).param("limit", RECENT_TURNS)
                .query((row, index) -> new RecentTurn(row.getLong("id"), row.getString("question"), row.getString("answer")))
                .list().reversed();
    }

    private List<Context> recall(long owner, String model, long before, String message) {
        return jdbc.sql("""
                SELECT t.question, t.answer,
                    MATCH(t.question, t.answer) AGAINST (:message IN NATURAL LANGUAGE MODE) AS relevance
                """ + TURN_TABLES + MEMORY_SCOPE + """
                AND t.id < :before
                HAVING relevance > 0 ORDER BY relevance DESC, id DESC LIMIT 3
                """).param("owner", owner).param("model", model).param("before", before).param("message", message)
                .query((row, index) -> new Context(row.getString("question"), row.getString("answer"))).list();
    }

    private void cache(long owner, String model, List<RecentTurn> turns) {
        try {
            redis.opsForValue().set(cacheKey(owner, model), json.writeValueAsString(turns), MEMORY_TTL);
        } catch (DataAccessException | JacksonException ignored) {
            // The committed turn can still be read from MySQL.
        }
    }

    private static String cacheKey(long owner, String model) {
        return "doc-compare:chat:recent:" + owner + ":" + model;
    }

    private static Turn turn(ResultSet row, int index) throws SQLException {
        return new Turn(row.getLong("id"), row.getString("question"), row.getString("answer"),
                row.getTimestamp("created_at").toInstant(), row.getString("model"), row.getString("reasoning"));
    }

    record Send(UUID requestId, String message, String model) {}
    record Turn(long id, String question, String answer, Instant createdAt, String model, String reasoning) {}
    record Page(List<Turn> turns, Long nextBefore) {}
    private record RecentTurn(long id, String question, String answer) {}
    record Context(String question, String answer) {}
    record Prompt(String message, List<Context> recent, List<Context> recalled, String model) {}
}
