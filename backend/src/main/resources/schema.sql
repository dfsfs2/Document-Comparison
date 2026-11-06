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
