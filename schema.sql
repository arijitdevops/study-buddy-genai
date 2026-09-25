-- Study Buddy GenAI -- fallback DDL.
--
-- Alembic is the source of truth (`alembic upgrade head`). This file exists so
-- the schema can be created without Python -- for example by the MySQL
-- container's entrypoint, or when inspecting the design without running the
-- app. Keep it in step with backend/app/db/models.py.

CREATE DATABASE IF NOT EXISTS study_buddy
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE study_buddy;

-- ---------------------------------------------------------------------------
-- Students
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS students (
  id            CHAR(32)     NOT NULL,
  display_name  VARCHAR(64)  NOT NULL DEFAULT 'Student',
  grade         INT          NOT NULL DEFAULT 8,
  created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  CONSTRAINT ck_students_grade CHECK (grade BETWEEN 1 AND 12)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- Chat sessions
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chat_sessions (
  id          CHAR(32)     NOT NULL,
  student_id  CHAR(32)     NOT NULL,
  title       VARCHAR(160) NOT NULL DEFAULT 'New chat',
  subject     VARCHAR(64)  NULL,
  created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
                           ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY ix_chat_sessions_student_updated (student_id, updated_at),
  CONSTRAINT fk_chat_sessions_student
    FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- Messages (content is stored PII-redacted)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS messages (
  id           CHAR(32)  NOT NULL,
  session_id   CHAR(32)  NOT NULL,
  role         ENUM('user','assistant','system','tool') NOT NULL,
  content      LONGTEXT  NOT NULL,
  token_count  INT       NOT NULL DEFAULT 0,
  metadata     JSON      NULL,
  created_at   DATETIME  NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY ix_messages_session_created (session_id, created_at),
  CONSTRAINT fk_messages_session
    FOREIGN KEY (session_id) REFERENCES chat_sessions (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- Uploaded files
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS uploaded_files (
  id               CHAR(32)     NOT NULL,
  session_id       CHAR(32)     NOT NULL,
  original_name    VARCHAR(255) NOT NULL,
  stored_name      VARCHAR(255) NOT NULL,
  mime             VARCHAR(128) NOT NULL,
  size_bytes       BIGINT       NOT NULL DEFAULT 0,
  extracted_chars  INT          NOT NULL DEFAULT 0,
  status           ENUM('pending','extracting','ready','failed') NOT NULL DEFAULT 'pending',
  error            VARCHAR(512) NULL,
  created_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_uploaded_files_stored_name (stored_name),
  KEY ix_uploaded_files_session (session_id),
  CONSTRAINT fk_uploaded_files_session
    FOREIGN KEY (session_id) REFERENCES chat_sessions (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- Extracted chunks
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS file_chunks (
  id           CHAR(32) NOT NULL,
  file_id      CHAR(32) NOT NULL,
  chunk_index  INT      NOT NULL,
  content      TEXT     NOT NULL,
  page         INT      NULL,
  PRIMARY KEY (id),
  KEY ix_file_chunks_file_index (file_id, chunk_index),
  CONSTRAINT fk_file_chunks_file
    FOREIGN KEY (file_id) REFERENCES uploaded_files (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- Guardrail audit trail
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS guardrail_events (
  id          CHAR(32)      NOT NULL,
  session_id  CHAR(32)      NULL,
  message_id  CHAR(32)      NULL,
  guard       VARCHAR(64)   NOT NULL,
  verdict     ENUM('allow','soft_block','hard_block') NOT NULL,
  category    VARCHAR(64)   NULL,
  detail      VARCHAR(1024) NULL,
  created_at  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY ix_guardrail_events_session_created (session_id, created_at),
  CONSTRAINT fk_guardrail_events_session
    FOREIGN KEY (session_id) REFERENCES chat_sessions (id) ON DELETE CASCADE,
  CONSTRAINT fk_guardrail_events_message
    FOREIGN KEY (message_id) REFERENCES messages (id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
