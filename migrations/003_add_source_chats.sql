-- Migration 003: source_chats (whitelist/blacklist de grupos monitorados)
--
-- mode = 'allow' significa: só monitora chats cujo chat_id esteja na tabela.
-- mode = 'deny'  significa: monitora TODOS os chats, exceto os listados.
-- Quando a tabela está vazia, o bot monitora todos (comportamento padrão).

CREATE TABLE IF NOT EXISTS source_chats (
    chat_id INTEGER PRIMARY KEY,
    title TEXT,
    mode TEXT NOT NULL DEFAULT 'allow',
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_source_chats_mode ON source_chats(mode);

INSERT OR IGNORE INTO _schema_migrations (version) VALUES (3);