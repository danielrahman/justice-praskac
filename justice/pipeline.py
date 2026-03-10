from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Callable

from justice.ai import (
    build_basic_info,
    build_external_checks,
    build_highlights,
    extract_history_events,
    extract_people_and_owners,
    resolve_ai_analysis,
)
from justice.db import (
    fail_refresh_run,
    finish_refresh_run,
    get_profile_record,
    save_history_entry,
    set_profile_status,
    start_refresh_run,
    touch_recent_search,
)
from justice.documents import parse_document_list, pick_recent_financial_docs
from justice.extraction import (
    extract_financial_doc_data,
    finalize_financial_timeline,
    merge_doc_year_map,
)
from justice.scraping import clean_ico, fetch_extract
from justice.utils import BASE_UI, JUSTICE_DOCUMENT_WORKERS, PROFILE_FRESH_DAYS, PROFILE_PARSER_VERSION, logger, public_error_message


ProgressCallback = Callable[[str, dict[str, Any]], None]


def _emit(on_progress: ProgressCallback | None, event: str, payload: dict[str, Any]) -> None:
    if on_progress:
        on_progress(event, payload)


def _current_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _document_title(doc: dict[str, Any]) -> str:
    return str(doc.get("document_number") or doc.get("type") or "listina")


def _document_progress_label(doc: dict[str, Any]) -> str:
    year_hint = (doc.get("years") or [None])[0]
    candidate_count = len(doc.get("pdf_candidates") or [])
    label = f"{_document_title(doc)} · soubory {candidate_count}"
    if year_hint:
        label = f"{_document_title(doc)} · rok {year_hint} · soubory {candidate_count}"
    return label


def _failed_financial_doc_result(doc: dict[str, Any], exc: Exception) -> tuple[dict[str, Any], dict[int, dict[str, float]]]:
    error_message = public_error_message(exc)
    attachment_results: list[dict[str, Any]] = []
    for attachment in list(doc.get("pdf_candidates") or []):
        attachment_results.append(
            {
                "label": attachment.get("label"),
                "url": attachment.get("url"),
                "pdf_index": attachment.get("pdf_index"),
                "page_hint": attachment.get("page_hint"),
                "candidate_score": attachment.get("candidate_score") or 0,
                "page_count": attachment.get("page_hint") or 0,
                "extraction_mode": "failed",
                "metrics_found": [],
                "error": error_message,
            }
        )
    doc_copy = dict(doc)
    doc_copy["download_links"] = doc.get("download_links") or []
    doc_copy["candidate_files"] = attachment_results
    doc_copy["candidate_file_count"] = len(doc.get("pdf_candidates") or [])
    doc_copy["combined_metrics_found"] = []
    doc_copy["metrics_found"] = []
    doc_copy["page_count"] = sum(int(item.get("page_count") or 0) for item in attachment_results) or doc.get("pages", 0)
    doc_copy["extraction_scope"] = "all_candidate_files"
    doc_copy["extraction_mode"] = "failed" if attachment_results else "missing"
    if attachment_results:
        best_attachment = max(attachment_results, key=lambda item: (item.get("candidate_score") or 0, item.get("page_count") or 0))
        doc_copy["pdf_url"] = best_attachment.get("url")
        doc_copy["pdf_name"] = best_attachment.get("label")
    return doc_copy, {}


def _compute_source_hash(
    current_extract: dict[str, Any],
    full_extract: dict[str, Any],
    docs: list[dict[str, Any]],
) -> str:
    normalized = {
        "current_rows": current_extract.get("rows") or [],
        "full_rows": full_extract.get("rows") or [],
        "documents": [
            {
                "detail_url": doc.get("detail_url"),
                "document_id": doc.get("document_id"),
                "document_number": doc.get("document_number"),
                "filed_date": doc.get("filed_date"),
                "received_date": doc.get("received_date"),
                "years": doc.get("years") or [],
                "candidate_files": [
                    {
                        "pdf_index": item.get("pdf_index"),
                        "content_sha256": item.get("content_sha256"),
                        "metrics_found": item.get("metrics_found") or [],
                    }
                    for item in (doc.get("candidate_files") or [])
                ],
            }
            for doc in docs
        ],
    }
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def is_profile_stale(record: dict[str, Any] | None) -> bool:
    if not record:
        return True
    if str(record.get("status") or "fresh") == "stale":
        return True
    if str(record.get("parser_version") or "") != PROFILE_PARSER_VERSION:
        return True
    if PROFILE_FRESH_DAYS > 0:
        refreshed_at = str(record.get("refreshed_at") or "")
        if refreshed_at:
            try:
                refreshed_dt = datetime.fromisoformat(refreshed_at)
            except ValueError:
                return True
            if refreshed_dt < datetime.now().astimezone() - timedelta(days=PROFILE_FRESH_DAYS):
                return True
    return False


def load_cached_profile(subjekt_id: str) -> dict[str, Any] | None:
    record = get_profile_record(subjekt_id)
    if not record or is_profile_stale(record):
        if record and str(record.get("parser_version") or "") != PROFILE_PARSER_VERSION:
            set_profile_status(subjekt_id, "stale")
        return None
    try:
        profile = json.loads(record["profile_json"])
    except Exception:
        return None
    profile["cache_status"] = "cached"
    profile.setdefault("parser_version", record.get("parser_version"))
    profile.setdefault("source_hash", record.get("source_hash"))
    profile.setdefault("computed_at", record.get("computed_at"))
    profile.setdefault("refreshed_at", record.get("refreshed_at"))
    return profile


def run_company_pipeline(
    subjekt_id: str,
    *,
    force_refresh: bool = False,
    visitor_id: str | None = None,
    query: str | None = None,
    on_progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    record = get_profile_record(subjekt_id)
    cached = None if force_refresh else load_cached_profile(subjekt_id)
    if cached is not None:
        touch_recent_search(
            subjekt_id,
            query=query,
            visitor_id=visitor_id,
            ico=str(cached.get("ico") or ""),
            name=str(cached.get("name") or subjekt_id),
        )
        _emit(on_progress, "status", {"label": "Načítám uložený profil z mezipaměti"})
        return cached

    trigger = "manual_refresh" if force_refresh else "parser_bump" if record else "cache_miss"
    if record and is_profile_stale(record) and str(record.get("parser_version") or "") != PROFILE_PARSER_VERSION:
        set_profile_status(subjekt_id, "stale")
    run_id = start_refresh_run(
        subjekt_id,
        trigger=trigger,
        parser_version=PROFILE_PARSER_VERSION,
        requested_query=query,
        requested_by=visitor_id,
        source_hash_before=(record or {}).get("source_hash"),
    )
    try:
        _emit(
            on_progress,
            "status",
            {"label": "Spouštím novou extrakci z veřejných podkladů" if force_refresh else "Otevírám aktuální výpis firmy"},
        )
        current_extract = fetch_extract(subjekt_id, "PLATNY", force_refresh=force_refresh)
        basic_info_items = build_basic_info(current_extract)
        company_name = current_extract.get("basic_info", {}).get("Obchodní firma") or current_extract.get("subtitle") or "Společnost"
        ico = clean_ico(str(current_extract.get("basic_info", {}).get("Identifikační číslo", "")))
        _emit(
            on_progress,
            "preview",
            {
                "subject_id": subjekt_id,
                "name": company_name,
                "ico": ico,
                "basic_info": basic_info_items,
            },
        )

        _emit(on_progress, "status", {"label": "Čtu úplný výpis a historii změn"})
        full_extract = fetch_extract(subjekt_id, "UPLNY", force_refresh=force_refresh)
        people = extract_people_and_owners(current_extract)
        history = extract_history_events(full_extract)

        _emit(on_progress, "status", {"label": "Stahuji seznam listin ze Sbírky listin"})
        docs = parse_document_list(subjekt_id, force_refresh=force_refresh)
        relevant_docs = pick_recent_financial_docs(docs, max_years=5, force_refresh_details=force_refresh)
        _emit(
            on_progress,
            "status",
            {"label": f"Vybral jsem {len(relevant_docs)} relevantních listin a projdu všechny kandidátní PDF přílohy"},
        )

        timeline_map: dict[int, dict[str, Any]] = {}
        total_docs = len(relevant_docs)
        processed_docs: list[dict[str, Any]] = []
        if total_docs:
            max_workers = min(JUSTICE_DOCUMENT_WORKERS, total_docs)
            _emit(on_progress, "status", {"label": f"Zpracovávám až {max_workers} listiny paralelně"})
            results_by_index: list[tuple[dict[str, Any], dict[int, dict[str, float]]] | None] = [None] * total_docs
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_map = {
                    executor.submit(
                        extract_financial_doc_data,
                        doc,
                        company_name=company_name,
                        ico=ico,
                    ): (index, doc)
                    for index, doc in enumerate(relevant_docs)
                }
                completed_docs = 0
                for future in as_completed(future_map):
                    index, doc = future_map[future]
                    try:
                        doc_copy, year_map = future.result()
                    except Exception as exc:
                        logger.exception(
                            "run_company_pipeline document failed subjekt_id=%s detail_url=%s",
                            subjekt_id,
                            doc.get("detail_url"),
                        )
                        doc_copy, year_map = _failed_financial_doc_result(doc, exc)
                    results_by_index[index] = (doc_copy, year_map)
                    completed_docs += 1
                    metric_count = len(doc_copy.get("metrics_found") or [])
                    if metric_count:
                        label = f"Hotovo {completed_docs}/{total_docs}: {_document_progress_label(doc)} · {metric_count} metrik"
                    else:
                        label = f"Hotovo {completed_docs}/{total_docs}: {_document_progress_label(doc)} · bez čitelných metrik"
                    _emit(on_progress, "status", {"label": label})
            for result in results_by_index:
                if result is None:
                    continue
                doc_copy, year_map = result
                processed_docs.append(doc_copy)
                merge_doc_year_map(timeline_map, doc_copy, year_map)

        timeline = finalize_financial_timeline(timeline_map)
        overview, deep, praskac = build_highlights(timeline, processed_docs, history)

        _emit(on_progress, "status", {"label": "Kontroluji trendy, díry v letech a veřejné signály"})
        _emit(on_progress, "status", {"label": "Claude AI sestavuje finální profil a doplňuje shrnutí"})
        ai_analysis = resolve_ai_analysis(
            company_name=company_name,
            ico=ico,
            basic_info_items=basic_info_items,
            executives=people["executives"],
            owners=people["owners"],
            history=history,
            timeline=timeline,
            docs=processed_docs,
            overview_fallback=overview,
            deep_fallback=deep,
            praskac_fallback=praskac,
        )

        external_checks = build_external_checks(timeline, company_name, ico)
        computed_at = _current_iso()
        source_hash = _compute_source_hash(current_extract, full_extract, processed_docs)
        profile = {
            "subject_id": subjekt_id,
            "name": company_name,
            "ico": ico,
            "basic_info": basic_info_items,
            "executives": people["executives"],
            "owners": people["owners"],
            "statutory_bodies": people["bodies"],
            "financial_timeline": timeline,
            "financial_documents": processed_docs,
            "analysis_engine": ai_analysis["analysis_engine"],
            "analysis_model": ai_analysis.get("analysis_model"),
            "analysis_usage": ai_analysis.get("analysis_usage"),
            "analysis_overview": ai_analysis["analysis_overview"],
            "data_quality_note": ai_analysis["data_quality_note"],
            "insight_summary": ai_analysis["insight_summary"],
            "deep_insights": ai_analysis["deep_insights"],
            "praskac": ai_analysis["praskac"],
            "history_signals": history,
            "external_checks": external_checks,
            "source_links": {
                "current_extract": current_extract.get("url"),
                "full_extract": full_extract.get("url"),
                "documents": f"{BASE_UI}vypis-sl-firma?subjektId={subjekt_id}",
                "current_extract_pdf": current_extract.get("pdf_url"),
                "full_extract_pdf": full_extract.get("pdf_url"),
                "chytryrejstrik": external_checks.get("source_url") if external_checks else None,
            },
            "generated_at": computed_at,
            "computed_at": computed_at,
            "refreshed_at": computed_at,
            "parser_version": PROFILE_PARSER_VERSION,
            "source_hash": source_hash,
            "last_run_id": run_id,
            "cache_status": "fresh",
        }
        save_history_entry(visitor_id, profile, query=query)
        finish_refresh_run(run_id, source_hash_after=source_hash)
        return profile
    except Exception as exc:
        fail_refresh_run(run_id, str(exc))
        logger.exception(f"run_company_pipeline error subjekt_id={subjekt_id}")
        raise
