from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterable
from contextlib import contextmanager
from typing import Any

try:
    import libsql
except Exception:  # pragma: no cover - optional dependency for non-Turso environments
    libsql = None

from justice.utils import (
    DB_BACKEND,
    DB_PATH,
    DATABASE_URL,
    PROFILE_FRESH_DAYS,
    PROFILE_PARSER_VERSION,
    TURSO_AUTH_TOKEN,
    logger,
)


SCHEMA_SQL = """
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
"""


def _connect_sqlite():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _connect_turso():
    if libsql is None:
        raise RuntimeError("libsql dependency is missing.")
    if not DATABASE_URL or not TURSO_AUTH_TOKEN:
        raise RuntimeError("Missing Turso configuration.")
    conn = libsql.connect(
        database=DATABASE_URL,
        auth_token=TURSO_AUTH_TOKEN,
    )
    return conn


def get_db() -> Any:
    return _connect_turso() if DB_BACKEND == "turso" else _connect_sqlite()


@contextmanager
def open_db() -> Iterable[Any]:
    conn = get_db()
    try:
        yield conn
    finally:
        conn.close()


def _sync_conn(conn: Any) -> None:
    if DB_BACKEND == "turso" and hasattr(conn, "sync"):
        try:
            conn.sync()
        except ValueError as exc:
            if "Remote mode" not in str(exc):
                raise


def _row_to_dict(row: Any, columns: list[str]) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, sqlite3.Row):
        return {key: row[key] for key in row.keys()}
    if isinstance(row, dict):
        return dict(row)
    return {col: row[idx] for idx, col in enumerate(columns)}


def _fetchall_dicts(cursor: Any) -> list[dict[str, Any]]:
    rows = cursor.fetchall()
    columns = [col[0] for col in cursor.description or []]
    return [_row_to_dict(row, columns) for row in rows]


def _fetchone_dict(cursor: Any) -> dict[str, Any] | None:
    row = cursor.fetchone()
    if row is None:
        return None
    columns = [col[0] for col in cursor.description or []]
    return _row_to_dict(row, columns)


def init_db() -> None:
    with open_db() as conn:
        for statement in [part.strip() for part in SCHEMA_SQL.split(";") if part.strip()]:
            conn.execute(statement)
        conn.commit()
        _sync_conn(conn)
        logger.info(f"Database initialized backend={DB_BACKEND}")


def _ensure_company(
    conn: Any,
    subject_id: str,
    ico: str,
    name: str,
    query: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO companies (subject_id, ico, name, last_query, last_viewed_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(subject_id) DO UPDATE SET
            ico = excluded.ico,
            name = excluded.name,
            last_query = COALESCE(excluded.last_query, companies.last_query),
            last_viewed_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        """,
        (subject_id, ico, name, query),
    )


def touch_recent_search(
    subject_id: str,
    query: str | None = None,
    visitor_id: str | None = None,
    *,
    ico: str = "",
    name: str = "",
) -> None:
    with open_db() as conn:
        _ensure_company(conn, subject_id, ico, name or subject_id, query)
        conn.execute(
            """
            INSERT INTO recent_searches (subject_id, query, last_visitor_id, last_seen_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(subject_id) DO UPDATE SET
                query = COALESCE(excluded.query, recent_searches.query),
                last_visitor_id = excluded.last_visitor_id,
                last_seen_at = CURRENT_TIMESTAMP
            """,
            (subject_id, query, visitor_id),
        )
        conn.commit()
        _sync_conn(conn)


def save_history_entry(visitor_id: str | None, profile: dict[str, Any], query: str | None = None) -> None:
    subject_id = str(profile.get("subject_id") or "").strip()
    if not subject_id:
        return
    parsed_json = json.dumps(profile, ensure_ascii=False)
    computed_at = str(profile.get("computed_at") or profile.get("generated_at") or "")
    refreshed_at = str(profile.get("refreshed_at") or computed_at or profile.get("generated_at") or "")
    parser_version = str(profile.get("parser_version") or PROFILE_PARSER_VERSION)
    source_hash = str(profile.get("source_hash") or "") or None
    with open_db() as conn:
        _ensure_company(
            conn,
            subject_id,
            str(profile.get("ico") or ""),
            str(profile.get("name") or subject_id),
            query,
        )
        conn.execute(
            """
            INSERT INTO company_profiles (
                subject_id, profile_json, parser_version, source_hash, status,
                freshness_ttl_days, fresh_until, computed_at, refreshed_at,
                last_run_id, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 'fresh', ?, NULL, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(subject_id) DO UPDATE SET
                profile_json = excluded.profile_json,
                parser_version = excluded.parser_version,
                source_hash = excluded.source_hash,
                status = excluded.status,
                freshness_ttl_days = excluded.freshness_ttl_days,
                computed_at = excluded.computed_at,
                refreshed_at = excluded.refreshed_at,
                last_run_id = COALESCE(excluded.last_run_id, company_profiles.last_run_id),
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                subject_id,
                parsed_json,
                parser_version,
                source_hash,
                PROFILE_FRESH_DAYS if PROFILE_FRESH_DAYS > 0 else None,
                computed_at,
                refreshed_at,
                profile.get("last_run_id"),
            ),
        )
        conn.execute(
            """
            INSERT INTO recent_searches (subject_id, query, last_visitor_id, last_seen_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(subject_id) DO UPDATE SET
                query = COALESCE(excluded.query, recent_searches.query),
                last_visitor_id = excluded.last_visitor_id,
                last_seen_at = CURRENT_TIMESTAMP
            """,
            (subject_id, query, visitor_id),
        )
        conn.commit()
        _sync_conn(conn)
        logger.info(f"save_history_entry subject_id={subject_id} backend={DB_BACKEND}")


def get_profile_record(subject_id: str) -> dict[str, Any] | None:
    with open_db() as conn:
        row = _fetchone_dict(
            conn.execute(
                """
                SELECT subject_id, profile_json, parser_version, source_hash, status,
                       freshness_ttl_days, fresh_until, computed_at, refreshed_at,
                       last_run_id, created_at, updated_at
                FROM company_profiles
                WHERE subject_id = ?
                LIMIT 1
                """,
                (subject_id,),
            )
        )
    return row


def get_history_profile(subject_id: str) -> dict[str, Any] | None:
    row = get_profile_record(subject_id)
    if not row:
        return None
    try:
        profile = json.loads(row["profile_json"])
    except Exception:
        return None
    profile.setdefault("parser_version", row.get("parser_version"))
    profile.setdefault("source_hash", row.get("source_hash"))
    profile.setdefault("computed_at", row.get("computed_at"))
    profile.setdefault("refreshed_at", row.get("refreshed_at"))
    return profile


def set_profile_status(subject_id: str, status: str) -> None:
    with open_db() as conn:
        conn.execute(
            "UPDATE company_profiles SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE subject_id = ?",
            (status, subject_id),
        )
        conn.commit()
        _sync_conn(conn)


def get_history_entries(visitor_id: str | None = None, limit: int = 40, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
    del visitor_id
    limit = max(1, limit)
    offset = max(0, offset)
    with open_db() as conn:
        total_row = _fetchone_dict(
            conn.execute("SELECT COUNT(*) AS count FROM recent_searches")
        )
        rows = _fetchall_dicts(
            conn.execute(
                """
                SELECT rs.subject_id, c.ico, c.name, rs.query, rs.last_seen_at AS updated_at
                FROM recent_searches rs
                JOIN companies c ON c.subject_id = rs.subject_id
                ORDER BY rs.last_seen_at DESC, c.updated_at DESC
                LIMIT ?
                OFFSET ?
                """,
                (limit, offset),
            )
        )
    items = [
        {
            "subject_id": row["subject_id"],
            "ico": row.get("ico"),
            "name": row.get("name"),
            "query": row.get("query"),
            "updated_at": row.get("updated_at"),
        }
        for row in rows
    ]
    total = int((total_row or {}).get("count") or 0)
    return items, total


def start_refresh_run(
    subject_id: str,
    trigger: str,
    parser_version: str,
    requested_query: str | None = None,
    requested_by: str | None = None,
    source_hash_before: str | None = None,
) -> str:
    run_id = str(uuid.uuid4())
    with open_db() as conn:
        _ensure_company(conn, subject_id, "", subject_id, requested_query)
        conn.execute(
            """
            INSERT INTO refresh_runs (
                id, subject_id, trigger, requested_query, requested_by,
                status, parser_version, source_hash_before, started_at
            )
            VALUES (?, ?, ?, ?, ?, 'started', ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                run_id,
                subject_id,
                trigger,
                requested_query,
                requested_by,
                parser_version,
                source_hash_before,
            ),
        )
        conn.commit()
        _sync_conn(conn)
    return run_id


def finish_refresh_run(run_id: str, source_hash_after: str | None = None) -> None:
    with open_db() as conn:
        conn.execute(
            """
            UPDATE refresh_runs
            SET status = 'completed', source_hash_after = ?, finished_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (source_hash_after, run_id),
        )
        conn.commit()
        _sync_conn(conn)


def fail_refresh_run(run_id: str, error_message: str) -> None:
    with open_db() as conn:
        conn.execute(
            """
            UPDATE refresh_runs
            SET status = 'failed', error_message = ?, finished_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (error_message, run_id),
        )
        conn.commit()
        _sync_conn(conn)


def get_refresh_runs(subject_id: str) -> list[dict[str, Any]]:
    with open_db() as conn:
        return _fetchall_dicts(
            conn.execute(
                """
                SELECT id, subject_id, trigger, requested_query, requested_by,
                       status, parser_version, source_hash_before, source_hash_after,
                       error_message, started_at, finished_at
                FROM refresh_runs
                WHERE subject_id = ?
                ORDER BY started_at DESC
                """,
                (subject_id,),
            )
        )


def upsert_document(record: dict[str, Any]) -> None:
    with open_db() as conn:
        _ensure_company(
            conn,
            str(record["subject_id"]),
            str(record.get("ico") or ""),
            str(record.get("company_name") or record["subject_id"]),
            None,
        )
        conn.execute(
            """
            INSERT INTO documents (
                subject_id, detail_url, pdf_index, content_sha256, source_url, r2_pdf_key,
                r2_text_key, text_kind, document_id, spis, document_number, doc_type,
                primary_year, created_date, received_date, filed_date, page_count,
                extraction_mode, metrics_found_json, used_in_profile, parser_version,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(subject_id, content_sha256) DO UPDATE SET
                detail_url = excluded.detail_url,
                pdf_index = excluded.pdf_index,
                source_url = excluded.source_url,
                r2_pdf_key = excluded.r2_pdf_key,
                r2_text_key = excluded.r2_text_key,
                text_kind = excluded.text_kind,
                document_id = excluded.document_id,
                spis = excluded.spis,
                document_number = excluded.document_number,
                doc_type = excluded.doc_type,
                primary_year = excluded.primary_year,
                created_date = excluded.created_date,
                received_date = excluded.received_date,
                filed_date = excluded.filed_date,
                page_count = excluded.page_count,
                extraction_mode = excluded.extraction_mode,
                metrics_found_json = excluded.metrics_found_json,
                used_in_profile = excluded.used_in_profile,
                parser_version = excluded.parser_version,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                record["subject_id"],
                record["detail_url"],
                int(record.get("pdf_index") or 0),
                record["content_sha256"],
                record.get("source_url"),
                record["r2_pdf_key"],
                record.get("r2_text_key"),
                record.get("text_kind"),
                record.get("document_id"),
                record.get("spis"),
                record.get("document_number"),
                record.get("doc_type"),
                record.get("primary_year"),
                record.get("created_date"),
                record.get("received_date"),
                record.get("filed_date"),
                record.get("page_count"),
                record.get("extraction_mode"),
                json.dumps(record.get("metrics_found") or [], ensure_ascii=False),
                1 if record.get("used_in_profile", True) else 0,
                record.get("parser_version") or PROFILE_PARSER_VERSION,
            ),
        )
        conn.commit()
        _sync_conn(conn)


def get_document_by_detail(detail_url: str, pdf_index: int = 0) -> dict[str, Any] | None:
    with open_db() as conn:
        row = _fetchone_dict(
            conn.execute(
                """
                SELECT id, subject_id, detail_url, pdf_index, content_sha256, source_url,
                       r2_pdf_key, r2_text_key, text_kind, document_id, spis,
                       document_number, doc_type, primary_year, created_date,
                       received_date, filed_date, page_count, extraction_mode,
                       metrics_found_json, used_in_profile, parser_version,
                       created_at, updated_at
                FROM documents
                WHERE detail_url = ? AND pdf_index = ?
                LIMIT 1
                """,
                (detail_url, pdf_index),
            )
        )
    if not row:
        return None
    try:
        row["metrics_found"] = json.loads(row.get("metrics_found_json") or "[]")
    except Exception:
        row["metrics_found"] = []
    return row
