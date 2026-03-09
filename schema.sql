-- Schema for app_state.db
-- Run: sqlite3 app_state.db < schema.sql

CREATE TABLE IF NOT EXISTS shared_company_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id TEXT NOT NULL UNIQUE,
    ico TEXT,
    name TEXT,
    query TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    last_visitor_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_shared_company_history_updated
    ON shared_company_history(updated_at DESC);
