package com.doccompare;

import java.util.List;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.server.ResponseStatusException;
import tools.jackson.databind.ObjectMapper;

@Service
class KnowledgeLibrary {
    private static final int MAX_RULES = 100;
    private final JdbcClient jdbc;
    private final TransactionTemplate transactions;
    private final ObjectMapper json;
    private final DocumentAgent agent;

    KnowledgeLibrary(JdbcClient jdbc, TransactionTemplate transactions, ObjectMapper json, DocumentAgent agent) {
        this.jdbc = jdbc;
        this.transactions = transactions;
        this.json = json;
        this.agent = agent;
    }

    List<Rule> rules(long owner) {
        return jdbc.sql("SELECT rules FROM user_knowledge WHERE owner_id = :owner")
                .param("owner", owner).query(String.class).optional()
                .map(this::parseRules).orElseGet(() -> initialize(owner));
    }

    Rule add(long owner, Draft draft) {
        String title = required(draft.title(), 80, "规则名称");
        String rule = required(draft.rule(), 1000, "规则内容");
        Rule entry = new Rule(UUID.randomUUID().toString(), title, rule, false);
        rules(owner);
        return transactions.execute(status -> {
            List<Rule> existing = lockRules(owner);
            if (existing.size() >= MAX_RULES) {
                throw new ResponseStatusException(HttpStatus.CONFLICT, "知识库最多保存 100 条规则。");
            }
            existing.add(entry);
            save(owner, existing);
            return entry;
        });
    }

    void remove(long owner, String id) {
        rules(owner);
        transactions.executeWithoutResult(status -> {
            List<Rule> existing = lockRules(owner);
            if (!existing.removeIf(entry -> entry.id().equals(id))) {
                throw new ResponseStatusException(HttpStatus.NOT_FOUND, "找不到这条知识规则。");
            }
            save(owner, existing);
        });
    }

    SearchResult query(long owner, Query query) {
        String text = required(query.query(), 300, "检索内容");
        return agent.queryKnowledge(new AgentQuery(text, query.useCloud(), rules(owner)));
    }

    private List<Rule> initialize(long owner) {
        List<Rule> defaults = agent.defaultKnowledge();
        if (defaults.size() > MAX_RULES) {
            throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "预置知识库超过 100 条规则。");
        }
        // Only the first request for this account seeds the row. An existing empty list is intentional.
        jdbc.sql("INSERT IGNORE INTO user_knowledge (owner_id, rules) VALUES (:owner, :rules)")
                .param("owner", owner).param("rules", json.writeValueAsString(defaults)).update();
        return parseRules(jdbc.sql("SELECT rules FROM user_knowledge WHERE owner_id = :owner")
                .param("owner", owner).query(String.class).single());
    }

    private List<Rule> lockRules(long owner) {
        return parseRules(jdbc.sql("SELECT rules FROM user_knowledge WHERE owner_id = :owner FOR UPDATE")
                .param("owner", owner).query(String.class).single());
    }

    private void save(long owner, List<Rule> rules) {
        jdbc.sql("UPDATE user_knowledge SET rules = :rules WHERE owner_id = :owner")
                .param("owner", owner).param("rules", json.writeValueAsString(rules)).update();
    }

    private List<Rule> parseRules(String value) {
        return json.readerForListOf(Rule.class).readValue(value);
    }

    private static String required(String value, int limit, String label) {
        if (value == null || value.isBlank() || value.strip().length() > limit) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, label + "须为 1–" + limit + " 个字符。");
        }
        return value.strip();
    }

    record Rule(String id, String title, String rule, boolean builtIn) {}
    record Draft(String title, String rule) {}
    record Query(String query, boolean useCloud) {}
    record AgentQuery(String query, boolean useCloud, List<Rule> knowledge) {}
    record SearchResult(List<Rule> matches, String mode) {}
}
