-- Migration 004: seen_hashes (deduplicação de ofertas entre grupos)
--
-- Guarda o hash normalizado do conteúdo de cada oferta notificada.
-- Se o mesmo hash aparecer de novo dentro da janela de deduplicação,
-- a notificação é suprimida.

CREATE TABLE IF NOT EXISTS seen_hashes (
    hash TEXT PRIMARY KEY,
    term TEXT,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    hit_count INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_seen_hashes_last_seen ON seen_hashes(last_seen DESC);

INSERT OR IGNORE INTO _schema_migrations (version) VALUES (4);