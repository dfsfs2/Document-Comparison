package com.doccompare;

import java.time.Duration;
import java.util.List;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestClientResponseException;
import org.springframework.web.server.ResponseStatusException;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

@Component
class DocumentAgent {
    private final RestClient client;
    private final ObjectMapper json;

    DocumentAgent(RestClient.Builder builder, ObjectMapper json, @Value("${app.agent.url}") String url,
                  @Value("${app.agent.token}") String token) {
        this.json = json;
        var factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(Duration.ofSeconds(5));
        factory.setReadTimeout(Duration.ofSeconds(120));
        this.client = builder.baseUrl(url).defaultHeader("X-Agent-Token", token)
                .requestFactory(factory).build();
    }

    Result format(byte[] reference, byte[] target, boolean useCloud, List<KnowledgeLibrary.Rule> knowledge) {
        var parts = new LinkedMultiValueMap<String, Object>();
        parts.add("reference", document(reference, "reference.docx"));
        parts.add("target", document(target, "target.docx"));
        parts.add("use_cloud", Boolean.toString(useCloud));
        var headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        parts.add("knowledge", new HttpEntity<>(json.writeValueAsString(knowledge), headers));
        try {
            Result result = client.post().uri("/format")
                    .contentType(MediaType.MULTIPART_FORM_DATA).body(parts)
                    .retrieve().body(Result.class);
            if (result == null || result.output() == null || result.output().length == 0
                    || result.mode() == null || result.rules() == null
                    || result.report() == null || !result.report().isObject()) {
                throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "文档处理服务返回了无效结果。");
            }
            return result;
        } catch (RestClientResponseException error) {
            throw documentFailure(error);
        } catch (RestClientException error) {
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "连接文档处理服务失败。");
        }
    }

    ExtractedText extract(byte[] bytes, String filename) {
        var parts = new LinkedMultiValueMap<String, Object>();
        parts.add("file", document(bytes, filename));
        try {
            ExtractedText result = client.post().uri("/knowledge/extract")
                    .contentType(MediaType.MULTIPART_FORM_DATA).body(parts)
                    .retrieve().body(ExtractedText.class);
            if (result != null && result.text() != null && !result.text().isBlank()) {
                return result;
            }
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "文档处理服务返回了无效的提取结果。");
        } catch (RestClientResponseException error) {
            throw documentFailure(error);
        } catch (RestClientException error) {
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "连接文档处理服务失败。");
        }
    }

    ChatAnswer chat(ChatMemory.Prompt prompt) {
        try {
            ChatAnswer result = client.post().uri("/chat").contentType(MediaType.APPLICATION_JSON)
                    .body(prompt).retrieve().body(ChatAnswer.class);
            if (result != null && result.answer() != null && !result.answer().isBlank()
                    && result.model() != null && !result.model().isBlank() && result.model().length() <= 128
                    && prompt.model().equals(result.model())) {
                return result;
            }
        } catch (RestClientResponseException error) {
            if (error.getStatusCode().value() == 503) {
                throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "百炼 API Key 尚未配置。");
            }
            if (error.getStatusCode().value() == 400) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "这个模型不在可选列表中，请刷新后重新选择。");
            }
            AgentError body = error.getStatusCode().value() == 502 ? error.getResponseBodyAs(AgentError.class) : null;
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY,
                    body == null ? "百炼暂时无法回复，请稍后重试。" : body.detail(), error);
        } catch (RestClientException error) {
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "连接聊天服务失败，请稍后重试。", error);
        }
        throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "百炼返回了空回复，请重试。");
    }

    ChatModels chatModels() {
        try {
            ChatModels result = client.get().uri("/chat/models").retrieve().body(ChatModels.class);
            if (result != null && result.models() != null && result.models().contains(result.defaultModel())) {
                return result;
            }
        } catch (RestClientException error) {
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "无法读取聊天模型列表，请稍后重试。", error);
        }
        throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "无法读取聊天模型列表，请稍后重试。");
    }

    private static ResponseStatusException documentFailure(RestClientResponseException error) {
        if (error.getStatusCode().value() == 422) {
            AgentError body = error.getResponseBodyAs(AgentError.class);
            return new ResponseStatusException(HttpStatus.UNPROCESSABLE_CONTENT,
                    body == null ? "文档格式无法处理。" : body.detail());
        }
        return new ResponseStatusException(HttpStatus.BAD_GATEWAY, "文档处理服务暂不可用。");
    }

    List<KnowledgeLibrary.Rule> defaultKnowledge() {
        try {
            List<KnowledgeLibrary.Rule> rules = client.get().uri("/knowledge/defaults").retrieve()
                    .body(new ParameterizedTypeReference<List<KnowledgeLibrary.Rule>>() {});
            if (rules != null) {
                return rules;
            }
        } catch (RestClientException error) {
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "无法读取预置知识库。", error);
        }
        throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "无法读取预置知识库。");
    }

    KnowledgeLibrary.SearchResult queryKnowledge(KnowledgeLibrary.AgentQuery query) {
        try {
            var result = client.post().uri("/knowledge/query").contentType(MediaType.APPLICATION_JSON)
                    .body(query).retrieve().body(KnowledgeLibrary.SearchResult.class);
            if (result != null && result.matches() != null && result.mode() != null) {
                return result;
            }
        } catch (RestClientException error) {
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "知识库检索服务暂不可用。", error);
        }
        throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "知识库检索服务暂不可用。");
    }

    private static ByteArrayResource document(byte[] bytes, String name) {
        return new ByteArrayResource(bytes) {
            @Override
            public String getFilename() {
                return name;
            }
        };
    }

    record AgentError(String detail) {}
    record ChatAnswer(String answer, String model, String reasoning) {}
    record ChatModels(String defaultModel, List<String> models) {}
    record ExtractedText(String text) {}
    record Result(byte[] output, int paragraphs, int tables, String mode, List<String> rules, JsonNode report) {}
}
