from __future__ import annotations

import time

import pytest

import justice.db as db_module
import justice.pipeline as pipeline


@pytest.fixture(autouse=True)
def _force_sqlite_backend(monkeypatch):
    monkeypatch.setattr(db_module, "DB_BACKEND", "sqlite")


def _stub_pipeline_dependencies(monkeypatch, docs, extract_impl, *, workers: int = 4):
    monkeypatch.setattr(pipeline, "JUSTICE_DOCUMENT_WORKERS", workers)
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
    monkeypatch.setattr(pipeline, "parse_document_list", lambda subject_id, force_refresh=False: docs)
    monkeypatch.setattr(pipeline, "pick_recent_financial_docs", lambda items, max_years=5, force_refresh_details=False: items)
    monkeypatch.setattr(pipeline, "extract_financial_doc_data", extract_impl)
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

    docs = [{"document_number": "C 1/SL1", "type": "účetní závěrka [2024]", "years": [2024], "detail_url": "https://or.justice.cz/doc/1", "subjekt_id": "123456"}]
    _stub_pipeline_dependencies(
        monkeypatch,
        docs,
        lambda doc, company_name="", ico="": (
            {
                **doc,
                "metrics_found": ["assets"],
                "candidate_files": [{"pdf_index": 0, "content_sha256": "abc123", "metrics_found": ["assets"]}],
            },
            {2024: {"assets": 10.0}},
        ),
    )

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


def test_run_company_pipeline_parallel_docs_preserve_input_order(tmp_path, monkeypatch):
    db_module.DB_PATH = tmp_path / "pipeline-parallel.db"
    db_module.init_db()
    docs = [
        {
            "document_number": "C 1/SL1",
            "type": "účetní závěrka [2024]",
            "years": [2024],
            "detail_url": "https://or.justice.cz/doc/1",
            "subjekt_id": "123456",
            "pdf_candidates": [{"pdf_index": 0, "candidate_score": 10}],
        },
        {
            "document_number": "C 1/SL2",
            "type": "účetní závěrka [2023]",
            "years": [2023],
            "detail_url": "https://or.justice.cz/doc/2",
            "subjekt_id": "123456",
            "pdf_candidates": [{"pdf_index": 0, "candidate_score": 10}],
        },
        {
            "document_number": "C 1/SL3",
            "type": "účetní závěrka [2022]",
            "years": [2022],
            "detail_url": "https://or.justice.cz/doc/3",
            "subjekt_id": "123456",
            "pdf_candidates": [{"pdf_index": 0, "candidate_score": 10}],
        },
    ]
    delays = {
        "https://or.justice.cz/doc/1": 0.05,
        "https://or.justice.cz/doc/2": 0.01,
        "https://or.justice.cz/doc/3": 0.03,
    }

    def extract_impl(doc, company_name="", ico=""):
        time.sleep(delays[doc["detail_url"]])
        year = doc["years"][0]
        return (
            {
                **doc,
                "metrics_found": [f"assets-{year}"],
                "candidate_files": [{"pdf_index": 0, "content_sha256": doc["detail_url"], "metrics_found": [f"assets-{year}"]}],
            },
            {year: {"assets": float(year)}},
        )

    _stub_pipeline_dependencies(monkeypatch, docs, extract_impl, workers=2)

    events: list[tuple[str, dict]] = []
    profile = pipeline.run_company_pipeline(
        "123456",
        visitor_id="visitor-1",
        query="firma fresh",
        force_refresh=True,
        on_progress=lambda event, payload: events.append((event, payload)),
    )

    assert [doc["detail_url"] for doc in profile["financial_documents"]] == [doc["detail_url"] for doc in docs]
    status_labels = [payload["label"] for event, payload in events if event == "status"]
    assert "Zpracovávám až 2 listiny paralelně" in status_labels
    hotovo_labels = [label for label in status_labels if label.startswith("Hotovo ")]
    assert hotovo_labels[0].startswith("Hotovo 1/3: C 1/SL2")


def test_run_company_pipeline_document_failure_stays_local(tmp_path, monkeypatch):
    db_module.DB_PATH = tmp_path / "pipeline-failure.db"
    db_module.init_db()
    docs = [
        {
            "document_number": "C 1/SL1",
            "type": "účetní závěrka [2024]",
            "years": [2024],
            "detail_url": "https://or.justice.cz/doc/good",
            "subjekt_id": "123456",
            "pdf_candidates": [{"label": "good.pdf", "url": "https://or.justice.cz/doc/good.pdf", "pdf_index": 0, "candidate_score": 15, "page_hint": 3}],
        },
        {
            "document_number": "C 1/SL2",
            "type": "účetní závěrka [2023]",
            "years": [2023],
            "detail_url": "https://or.justice.cz/doc/bad",
            "subjekt_id": "123456",
            "pdf_candidates": [{"label": "bad.pdf", "url": "https://or.justice.cz/doc/bad.pdf", "pdf_index": 0, "candidate_score": 8, "page_hint": 2}],
        },
    ]

    def extract_impl(doc, company_name="", ico=""):
        if doc["detail_url"].endswith("/bad"):
            raise RuntimeError("boom")
        year = doc["years"][0]
        return (
            {
                **doc,
                "metrics_found": ["assets"],
                "candidate_files": [{"pdf_index": 0, "content_sha256": "good-sha", "metrics_found": ["assets"]}],
            },
            {year: {"assets": 10.0}},
        )

    _stub_pipeline_dependencies(monkeypatch, docs, extract_impl, workers=2)

    profile = pipeline.run_company_pipeline(
        "123456",
        visitor_id="visitor-1",
        query="firma fresh",
        force_refresh=True,
    )

    failed_doc = next(doc for doc in profile["financial_documents"] if doc["detail_url"].endswith("/bad"))
    assert failed_doc["extraction_mode"] == "failed"
    assert failed_doc["candidate_files"][0]["error"] == "boom"
    assert any(doc["detail_url"].endswith("/good") for doc in profile["financial_documents"])
