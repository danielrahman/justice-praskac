from __future__ import annotations

import pytest

import justice.db as db_module
import justice.pipeline as pipeline


@pytest.fixture(autouse=True)
def _force_sqlite_backend(monkeypatch):
    monkeypatch.setattr(db_module, "DB_BACKEND", "sqlite")


def test_run_company_pipeline_returns_cached_profile(tmp_path):
    db_module.DB_PATH = tmp_path / "pipeline-cache.db"
    db_module.init_db()
    db_module.save_history_entry(
        None,
        {
            "subject_id": "123456",
            "ico": "12345678",
            "name": "Firma Cached",
            "computed_at": "2026-03-10T12:00:00+00:00",
            "refreshed_at": "2026-03-10T12:00:00+00:00",
            "parser_version": pipeline.PROFILE_PARSER_VERSION,
            "source_hash": "cached-hash",
            "cache_status": "fresh",
        },
        query="firma cached",
    )

    events: list[tuple[str, dict]] = []
    profile = pipeline.run_company_pipeline(
        "123456",
        query="firma cached",
        on_progress=lambda event, payload: events.append((event, payload)),
    )

    assert profile["cache_status"] == "cached"
    assert events == [("status", {"label": "Načítám uložený profil z mezipaměti"})]


def test_run_company_pipeline_refreshes_and_persists_run(tmp_path, monkeypatch):
    db_module.DB_PATH = tmp_path / "pipeline-refresh.db"
    db_module.init_db()

    monkeypatch.setattr(
        pipeline,
        "fetch_extract",
        lambda subject_id, typ, force_refresh=False: {
            "url": f"https://or.justice.cz/{subject_id}/{typ}",
            "rows": [{"label": "Obchodní firma", "value": "Firma Fresh", "history": ""}],
            "basic_info": {"Obchodní firma": "Firma Fresh", "Identifikační číslo": "12345678"},
            "subtitle": "Firma Fresh",
            "pdf_url": f"https://or.justice.cz/{subject_id}/{typ}.pdf",
        },
    )
    monkeypatch.setattr(pipeline, "build_basic_info", lambda extract: [{"label": "Obchodní firma", "value": "Firma Fresh"}])
    monkeypatch.setattr(
        pipeline,
        "extract_people_and_owners",
        lambda extract: {"executives": [], "owners": [], "bodies": []},
    )
    monkeypatch.setattr(
        pipeline,
        "extract_history_events",
        lambda extract: {"name_changes": 0, "address_changes": 0, "management_turnover": 0},
    )
    monkeypatch.setattr(
        pipeline,
        "parse_document_list",
        lambda subject_id, force_refresh=False: [{"document_number": "C 1/SL1", "type": "účetní závěrka [2024]", "years": [2024], "detail_url": "https://or.justice.cz/doc/1", "subjekt_id": subject_id}],
    )
    monkeypatch.setattr(pipeline, "pick_recent_financial_docs", lambda docs, max_years=5, force_refresh_details=False: docs)
    monkeypatch.setattr(
        pipeline,
        "extract_financial_doc_data",
        lambda doc, company_name="", ico="": (
            {
                **doc,
                "metrics_found": ["assets"],
                "candidate_files": [{"pdf_index": 0, "content_sha256": "abc123", "metrics_found": ["assets"]}],
            },
            {2024: {"assets": 10.0}},
        ),
    )
    monkeypatch.setattr(pipeline, "build_highlights", lambda timeline, docs, history: ([{"title": "OK", "detail": "OK"}], [], []))
    monkeypatch.setattr(
        pipeline,
        "resolve_ai_analysis",
        lambda **kwargs: {
            "analysis_engine": "disabled",
            "analysis_model": None,
            "analysis_usage": None,
            "analysis_overview": "Fallback",
            "data_quality_note": "OK",
            "insight_summary": [{"title": "OK", "detail": "OK"}],
            "deep_insights": [],
            "praskac": [],
        },
    )
    monkeypatch.setattr(pipeline, "build_external_checks", lambda timeline, company_name, ico: None)

    events: list[tuple[str, dict]] = []
    profile = pipeline.run_company_pipeline(
        "123456",
        visitor_id="visitor-1",
        query="firma fresh",
        force_refresh=True,
        on_progress=lambda event, payload: events.append((event, payload)),
    )

    assert profile["cache_status"] == "fresh"
    assert profile["parser_version"] == pipeline.PROFILE_PARSER_VERSION
    assert profile["source_hash"]
    runs = db_module.get_refresh_runs("123456")
    assert len(runs) == 1
    assert runs[0]["status"] == "completed"
    assert runs[0]["trigger"] == "manual_refresh"
    assert any(event == "preview" for event, _ in events)
