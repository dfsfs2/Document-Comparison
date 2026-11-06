package com.doccompare;

import java.time.Duration;
import java.util.List;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.io.ByteArrayResource;
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

@Component
class DocumentAgent {
    private final RestClient client;

    DocumentAgent(RestClient.Builder builder, @Value("${app.agent.url}") String url,
                  @Value("${app.agent.token}") String token) {
        var factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(Duration.ofSeconds(5));
        factory.setReadTimeout(Duration.ofSeconds(120));
        this.client = builder.baseUrl(url).defaultHeader("X-Agent-Token", token)
                .requestFactory(factory).build();
    }

    Result format(byte[] reference, byte[] target, boolean useCloud) {
        var parts = new LinkedMultiValueMap<String, Object>();
        parts.add("reference", document(reference, "reference.docx"));
        parts.add("target", document(target, "target.docx"));
        parts.add("use_cloud", Boolean.toString(useCloud));
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
