CREATE TABLE IF NOT EXISTS app_user (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL UNIQUE,
    password_hash VARCHAR(100) NOT NULL
);

CREATE TABLE IF NOT EXISTS document_job (
    id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
    owner_id BIGINT NOT NULL,
    reference_name VARCHAR(255) NOT NULL,
    target_name VARCHAR(255) NOT NULL,
    paragraph_count INT NOT NULL,
    table_count INT NOT NULL,
    mode VARCHAR(24) NOT NULL,
    rules VARCHAR(1000) NOT NULL,
    output LONGBLOB NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    FOREIGN KEY (owner_id) REFERENCES app_user (id),
    INDEX job_owner_date (owner_id, created_at)
) DEFAULT CHARACTER SET utf8mb4;

CREATE TABLE IF NOT EXISTS document_job_report (
    job_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
    details JSON NOT NULL,
    FOREIGN KEY (job_id) REFERENCES document_job (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS user_knowledge (
    owner_id BIGINT NOT NULL PRIMARY KEY,
    rules JSON NOT NULL,
    FOREIGN KEY (owner_id) REFERENCES app_user (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS chat_turn (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    owner_id BIGINT NOT NULL,
    request_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    FOREIGN KEY (owner_id) REFERENCES app_user (id) ON DELETE CASCADE,
    UNIQUE KEY chat_owner_request (owner_id, request_id),
    INDEX chat_owner_turn (owner_id, id),
    FULLTEXT KEY chat_turn_search (question, answer) WITH PARSER ngram
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4;

CREATE TABLE IF NOT EXISTS chat_turn_model (
    turn_id BIGINT NOT NULL PRIMARY KEY,
    model VARCHAR(128) NOT NULL,
    FOREIGN KEY (turn_id) REFERENCES chat_turn (id) ON DELETE CASCADE
) DEFAULT CHARACTER SET utf8mb4;

CREATE TABLE IF NOT EXISTS chat_turn_details (
    turn_id BIGINT NOT NULL PRIMARY KEY,
    reasoning TEXT NULL,
    FOREIGN KEY (turn_id) REFERENCES chat_turn (id) ON DELETE CASCADE
) DEFAULT CHARACTER SET utf8mb4;

CREATE TABLE IF NOT EXISTS chat_turn_attachments (
    turn_id BIGINT NOT NULL PRIMARY KEY,
    names JSON NOT NULL,
    fingerprint CHAR(64) CHARACTER SET ascii NOT NULL,
    FOREIGN KEY (turn_id) REFERENCES chat_turn (id) ON DELETE CASCADE
) DEFAULT CHARACTER SET utf8mb4;
