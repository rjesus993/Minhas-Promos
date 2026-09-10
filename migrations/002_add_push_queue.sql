-- Migration 002: Add push queue table
CREATE TABLE IF NOT EXISTS push_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    html TEXT NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_error TEXT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_push_queue_next_attempt ON push_queue(next_attempt_at ASC);

INSERT OR IGNORE INTO _schema_migrations (version) VALUES (2);
