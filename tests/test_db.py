from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import justice.app as app_module
import justice.db as db_module
from justice.pipeline import is_profile_stale


@pytest.fixture(autouse=True)
def _force_sqlite_backend(monkeypatch):
    monkeypatch.setattr(db_module, "DB_BACKEND", "sqlite")


def _seed_history(db_path: Path, count: int) -> None:
    db_module.DB_PATH = db_path
    db_module.init_db()
    for idx in range(count):
        subject_id = str(1000 + idx)
        db_module.save_history_entry(
            None,
            {
                "subject_id": subject_id,
                "ico": f"{idx + 1:08d}",
                "name": f"Firma {idx}",
                "computed_at": f"2026-03-10T12:{idx:02d}:00+00:00",
                "refreshed_at": f"2026-03-10T12:{idx:02d}:00+00:00",
                "parser_version": "test-parser",
                "source_hash": f"hash-{idx}",
            },
            query=f"dotaz {idx}",
        )
        conn = db_module.get_db()
        try:
            conn.execute(
                "UPDATE recent_searches SET last_seen_at = ? WHERE subject_id = ?",
                (f"2026-03-10 12:{idx:02d}:00", subject_id),
            )
            conn.execute(
                "UPDATE companies SET updated_at = ? WHERE subject_id = ?",
                (f"2026-03-10 12:{idx:02d}:00", subject_id),
            )
            conn.commit()
        finally:
            conn.close()


def test_get_history_entries_paginates(tmp_path):
    db_path = tmp_path / "history.db"
    _seed_history(db_path, 5)

    items, total = db_module.get_history_entries(limit=2, offset=1)

    assert total == 5
    assert [item["subject_id"] for item in items] == ["1003", "1002"]
    assert [item["query"] for item in items] == ["dotaz 3", "dotaz 2"]


def test_api_history_returns_metadata(tmp_path):
    db_path = tmp_path / "history-api.db"
    _seed_history(db_path, 3)
    db_module.DB_PATH = db_path

    with TestClient(app_module.app) as client:
        response = client.get("/api/history?limit=2&offset=1")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert data["limit"] == 2
    assert data["offset"] == 1
    assert data["has_more"] is False
    assert [item["subject_id"] for item in data["items"]] == ["1001", "1000"]


def test_api_company_stored_returns_cached_profile(tmp_path):
    db_path = tmp_path / "stored-profile-api.db"
    db_module.DB_PATH = db_path
    db_module.init_db()
    db_module.save_history_entry(
        None,
        {
            "subject_id": "123456",
            "ico": "12345678",
            "name": "Firma Nova",
            "computed_at": "2026-03-10T12:00:00+00:00",
            "refreshed_at": "2026-03-10T12:00:00+00:00",
            "parser_version": "v-test",
            "source_hash": "hash-123",
        },
        query="stary dotaz",
    )

    with TestClient(app_module.app) as client:
        response = client.get("/api/company/stored?subjektId=123456&q=novy%20dotaz")

    assert response.status_code == 200
    data = response.json()
    assert data["subject_id"] == "123456"
    assert data["cache_status"] == "cached"

    items, _ = db_module.get_history_entries(limit=1, offset=0)
    assert items[0]["subject_id"] == "123456"
    assert items[0]["query"] == "novy dotaz"


def test_get_history_profile_returns_enriched_metadata(tmp_path):
    db_path = tmp_path / "profile.db"
    db_module.DB_PATH = db_path
    db_module.init_db()
    db_module.save_history_entry(
        None,
        {
            "subject_id": "123456",
            "ico": "12345678",
            "name": "Firma Nova",
            "computed_at": "2026-03-10T12:00:00+00:00",
            "refreshed_at": "2026-03-10T12:00:00+00:00",
            "parser_version": "v-test",
            "source_hash": "hash-123",
        },
        query="firma nova",
    )

    profile = db_module.get_history_profile("123456")

    assert profile is not None
    assert profile["subject_id"] == "123456"
    assert profile["parser_version"] == "v-test"
    assert profile["source_hash"] == "hash-123"
    assert profile["computed_at"] == "2026-03-10T12:00:00+00:00"


def test_is_profile_stale_when_parser_version_differs():
    record = {
        "subject_id": "123456",
        "parser_version": "legacy",
        "status": "fresh",
        "refreshed_at": "2026-03-10T12:00:00+00:00",
    }

    assert is_profile_stale(record) is True


def test_refresh_run_lifecycle(tmp_path):
    db_path = tmp_path / "refresh.db"
    db_module.DB_PATH = db_path
    db_module.init_db()
    db_module.save_history_entry(
        None,
        {
            "subject_id": "123456",
            "ico": "12345678",
            "name": "Firma Nova",
            "computed_at": "2026-03-10T12:00:00+00:00",
            "refreshed_at": "2026-03-10T12:00:00+00:00",
            "parser_version": "v-test",
        },
    )

    run_id = db_module.start_refresh_run(
        "123456",
        trigger="manual_refresh",
        parser_version="v-test",
        requested_query="firma nova",
        requested_by="visitor-1",
        source_hash_before="old-hash",
    )
    db_module.finish_refresh_run(run_id, source_hash_after="new-hash")

    runs = db_module.get_refresh_runs("123456")

    assert len(runs) == 1
    assert runs[0]["id"] == run_id
    assert runs[0]["status"] == "completed"
    assert runs[0]["source_hash_before"] == "old-hash"
    assert runs[0]["source_hash_after"] == "new-hash"


def test_document_upsert_and_lookup(tmp_path):
    db_path = tmp_path / "documents.db"
    db_module.DB_PATH = db_path
    db_module.init_db()

    db_module.upsert_document(
        {
            "subject_id": "123456",
            "ico": "12345678",
            "company_name": "Firma Nova",
            "detail_url": "https://or.justice.cz/ias/ui/vypis-sl-detail?dokument=1&subjektId=123456",
            "pdf_index": 0,
            "content_sha256": "abc123",
            "source_url": "https://or.justice.cz/ias/content/download?id=file1",
            "r2_pdf_key": "companies/123456/documents/abc123.pdf",
            "r2_text_key": "companies/123456/documents/abc123.txt",
            "text_kind": "selected_extract",
            "document_id": "1",
            "spis": "10",
            "document_number": "C 1/SL1",
            "doc_type": "účetní závěrka [2024]",
            "primary_year": 2024,
            "created_date": "2025-01-10",
            "received_date": "2025-01-11",
            "filed_date": "2025-01-12",
            "page_count": 12,
            "extraction_mode": "digital",
            "metrics_found": ["assets", "revenue"],
            "used_in_profile": True,
            "parser_version": "v-test",
        }
    )

    doc = db_module.get_document_by_detail(
        "https://or.justice.cz/ias/ui/vypis-sl-detail?dokument=1&subjektId=123456",
        0,
    )

    assert doc is not None
    assert doc["content_sha256"] == "abc123"
    assert doc["r2_pdf_key"] == "companies/123456/documents/abc123.pdf"
    assert doc["metrics_found"] == ["assets", "revenue"]
