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
            if (error.getStatusCode().value() == 422) {
                AgentError body = error.getResponseBodyAs(AgentError.class);
                throw new ResponseStatusException(HttpStatus.UNPROCESSABLE_CONTENT,
                        body == null ? "文档格式无法处理。" : body.detail());
            }
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "文档处理服务暂不可用。");
        } catch (RestClientException error) {
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "连接文档处理服务失败。");
        }
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
    record Result(byte[] output, int paragraphs, int tables, String mode, List<String> rules, JsonNode report) {}
}
