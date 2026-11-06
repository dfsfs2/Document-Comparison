package com.doccompare;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.Principal;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.util.List;
import java.util.Locale;
import java.util.UUID;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.http.ContentDisposition;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.web.csrf.CsrfToken;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;

@RestController
@RequestMapping("/api")
class WorkspaceController {
    private static final MediaType DOCX = MediaType.parseMediaType(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document");
    private static final String JOB_FIELDS =
            "id, reference_name, target_name, paragraph_count, table_count, mode, rules, created_at";
    private final JdbcClient jdbc;
    private final PasswordEncoder passwords;
    private final DocumentAgent agent;

    WorkspaceController(JdbcClient jdbc, PasswordEncoder passwords, DocumentAgent agent) {
        this.jdbc = jdbc;
        this.passwords = passwords;
        this.agent = agent;
    }

    @GetMapping("/session")
    Session session(Principal user, CsrfToken token) {
        return new Session(user == null ? null : user.getName(), token.getHeaderName(), token.getToken());
    }

    @PostMapping("/auth/register")
    @ResponseStatus(HttpStatus.CREATED)
    void register(@RequestBody Credentials credentials) {
        String username = credentials.username();
        String password = credentials.password();
        if (username == null || !username.matches("[a-zA-Z0-9_]{3,32}")) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "账号须为 3–32 位字母、数字或下划线。");
        }
        if (password == null || password.length() < 8 || password.getBytes(StandardCharsets.UTF_8).length > 72) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "密码至少 8 个字符，UTF-8 编码不超过 72 字节。");
        }
        try {
            jdbc.sql("INSERT INTO app_user (username, password_hash) VALUES (:username, :hash)")
                    .param("username", username).param("hash", passwords.encode(password)).update();
        } catch (DuplicateKeyException error) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "这个账号已存在。");
        }
    }

    @GetMapping("/jobs")
    List<Job> jobs(Principal user) {
        return jdbc.sql("SELECT " + JOB_FIELDS + " FROM document_job WHERE owner_id = :owner "
                        + "ORDER BY created_at DESC LIMIT 50")
                .param("owner", ownerId(user)).query(WorkspaceController::job).list();
    }

    @PostMapping(path = "/jobs", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    @ResponseStatus(HttpStatus.CREATED)
    Job create(Principal user, @RequestParam MultipartFile reference, @RequestParam MultipartFile target,
               @RequestParam(defaultValue = "false") boolean useCloud) throws IOException {
        String referenceName = filename(reference);
        String targetName = filename(target);
        long owner = ownerId(user);
        var result = agent.format(reference.getBytes(), target.getBytes(), useCloud);
        String id = UUID.randomUUID().toString();
        jdbc.sql("""
                INSERT INTO document_job (id, owner_id, reference_name, target_name,
                    paragraph_count, table_count, mode, rules, output)
                VALUES (:id, :owner, :reference, :target, :paragraphs, :tables, :mode, :rules, :output)
                """).param("id", id).param("owner", owner).param("reference", referenceName).param("target", targetName)
                .param("paragraphs", result.paragraphs()).param("tables", result.tables()).param("mode", result.mode())
                .param("rules", String.join(",", result.rules())).param("output", result.output()).update();
        return jdbc.sql("SELECT " + JOB_FIELDS + " FROM document_job WHERE id = :id AND owner_id = :owner")
                .param("id", id).param("owner", owner).query(WorkspaceController::job).single();
    }

    @GetMapping("/jobs/{id}/download")
    ResponseEntity<byte[]> download(Principal user, @PathVariable UUID id) {
        return jdbc.sql("SELECT target_name, output FROM document_job WHERE id = :id AND owner_id = :owner")
                .param("id", id.toString()).param("owner", ownerId(user))
                .query((row, index) -> {
                    String original = row.getString("target_name");
                    String outputName = original.substring(0, original.length() - 5) + "_已套版.docx";
                    String disposition = ContentDisposition.attachment()
                            .filename(outputName, StandardCharsets.UTF_8).build().toString();
                    return ResponseEntity.ok().contentType(DOCX).header(HttpHeaders.CONTENT_DISPOSITION, disposition)
                            .body(row.getBytes("output"));
                }).optional().orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "找不到此任务。"));
    }

    private long ownerId(Principal user) {
        return jdbc.sql("SELECT id FROM app_user WHERE username = :username")
                .param("username", user.getName()).query(Long.class).single();
    }

    private static String filename(MultipartFile file) {
        String original = file.getOriginalFilename();
        if (original == null || file.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "请上传两个非空的 .docx 文件。");
        }
        String name = original.replace('\\', '/');
        name = name.substring(name.lastIndexOf('/') + 1);
        if (name.length() <= 5 || name.length() > 200 || name.chars().anyMatch(Character::isISOControl)
                || !name.toLowerCase(Locale.ROOT).endsWith(".docx")) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "仅支持文件名不超过 200 字符的 .docx。");
        }
        return name;
    }

    private static Job job(ResultSet row, int index) throws SQLException {
        String rules = row.getString("rules");
        return new Job(row.getString("id"), row.getString("reference_name"), row.getString("target_name"),
                row.getInt("paragraph_count"), row.getInt("table_count"), row.getString("mode"),
                rules.isEmpty() ? List.of() : List.of(rules.split(",")), row.getTimestamp("created_at").toInstant());
    }

    record Session(String username, String csrfHeader, String csrfToken) {}
    record Credentials(String username, String password) {}
    record Job(String id, String referenceName, String targetName, int paragraphs, int tables,
               String mode, List<String> rules, Instant createdAt) {}
}
