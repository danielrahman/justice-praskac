from __future__ import annotations

import json
import sqlite3
from typing import Any

from justice.utils import DB_PATH, logger


def init_db() -> None:
    """Create tables and indexes once at startup."""
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
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
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_shared_company_history_updated ON shared_company_history(updated_at DESC)"
        )
        conn.commit()
        logger.info("Database initialized")
    finally:
        conn.close()


def get_db() -> sqlite3.Connection:
    """Open a database connection. Caller must close it."""
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def save_history_entry(visitor_id: str | None, profile: dict[str, Any], query: str | None = None) -> None:
    subject_id = str(profile.get("subject_id") or "")
    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO shared_company_history (subject_id, ico, name, query, payload_json, created_at, updated_at, last_visitor_id)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?)
            ON CONFLICT(subject_id)
            DO UPDATE SET
                ico = excluded.ico,
                name = excluded.name,
                query = COALESCE(excluded.query, shared_company_history.query),
                payload_json = excluded.payload_json,
                updated_at = CURRENT_TIMESTAMP,
                last_visitor_id = excluded.last_visitor_id
            """,
            (
                subject_id,
                str(profile.get("ico") or ""),
                str(profile.get("name") or ""),
                query,
                json.dumps(profile, ensure_ascii=False),
                visitor_id,
            ),
        )
        conn.commit()
        logger.info(f"save_history_entry subject_id={subject_id}")
    finally:
        conn.close()


def get_history_entries(_visitor_id: str | None = None, limit: int = 40) -> list[dict[str, Any]]:
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT subject_id, ico, name, query, payload_json, updated_at
            FROM shared_company_history
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    items: list[dict[str, Any]] = []
    for row in rows:
        items.append(
            {
                "subject_id": row["subject_id"],
                "ico": row["ico"],
                "name": row["name"],
                "query": row["query"],
                "updated_at": row["updated_at"],
            }
        )
    return items
