-- Schema for Turso/libSQL (also works with local SQLite fallback)

CREATE TABLE IF NOT EXISTS companies (
    subject_id TEXT PRIMARY KEY,
    ico TEXT,
    name TEXT NOT NULL,
    last_query TEXT,
    last_viewed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_companies_ico ON companies(ico);
CREATE INDEX IF NOT EXISTS idx_companies_last_viewed_at ON companies(last_viewed_at DESC);

CREATE TABLE IF NOT EXISTS company_profiles (
    subject_id TEXT PRIMARY KEY REFERENCES companies(subject_id) ON DELETE CASCADE,
    profile_json TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    source_hash TEXT,
    status TEXT NOT NULL DEFAULT 'fresh' CHECK (status IN ('fresh', 'stale')),
    freshness_ttl_days INTEGER,
    fresh_until TEXT,
    computed_at TEXT NOT NULL,
    refreshed_at TEXT NOT NULL,
    last_run_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_company_profiles_refreshed_at
    ON company_profiles(refreshed_at DESC);

CREATE TABLE IF NOT EXISTS refresh_runs (
    id TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL REFERENCES companies(subject_id) ON DELETE CASCADE,
    trigger TEXT NOT NULL CHECK (trigger IN ('cache_miss', 'parser_bump', 'manual_refresh')),
    requested_query TEXT,
    requested_by TEXT,
    status TEXT NOT NULL CHECK (status IN ('started', 'completed', 'failed')),
    parser_version TEXT NOT NULL,
    source_hash_before TEXT,
    source_hash_after TEXT,
    error_message TEXT,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_refresh_runs_subject_started_at
    ON refresh_runs(subject_id, started_at DESC);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id TEXT NOT NULL REFERENCES companies(subject_id) ON DELETE CASCADE,
    detail_url TEXT NOT NULL,
    pdf_index INTEGER NOT NULL DEFAULT 0,
    content_sha256 TEXT NOT NULL,
    source_url TEXT,
    r2_pdf_key TEXT NOT NULL,
    r2_text_key TEXT,
    text_kind TEXT,
    document_id TEXT,
    spis TEXT,
    document_number TEXT,
    doc_type TEXT,
    primary_year INTEGER,
    created_date TEXT,
    received_date TEXT,
    filed_date TEXT,
    page_count INTEGER,
    extraction_mode TEXT,
    metrics_found_json TEXT,
    used_in_profile INTEGER NOT NULL DEFAULT 1,
    parser_version TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(subject_id, content_sha256),
    UNIQUE(detail_url, pdf_index)
);

CREATE INDEX IF NOT EXISTS idx_documents_subject_year
    ON documents(subject_id, primary_year DESC);

CREATE INDEX IF NOT EXISTS idx_documents_sha
    ON documents(content_sha256);

CREATE TABLE IF NOT EXISTS recent_searches (
    subject_id TEXT PRIMARY KEY REFERENCES companies(subject_id) ON DELETE CASCADE,
    query TEXT,
    last_visitor_id TEXT,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_recent_searches_last_seen_at
    ON recent_searches(last_seen_at DESC);
