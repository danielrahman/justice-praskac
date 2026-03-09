from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.responses import JSONResponse

from justice.ai import (
    build_basic_info,
    build_company_profile,
    build_external_checks,
    build_highlights,
    extract_history_events,
    extract_people_and_owners,
    generate_ai_analysis,
)
from justice.db import get_history_entries, init_db, save_history_entry
from justice.documents import parse_document_detail, parse_document_list, pick_recent_financial_docs
from justice.extraction import (
    extract_financial_doc_data,
    finalize_financial_timeline,
    merge_doc_year_map,
)
from justice.scraping import clean_ico, fetch_binary, fetch_extract, search_companies
from justice.utils import (
    AI_ENABLED,
    BASE_UI,
    PDF_DIR,
    PROFILE_CACHE_TTL_SECONDS,
    PROFILE_CACHE_VERSION,
    load_json_cache,
    logger,
    norm_text,
    public_error_message,
    save_json_cache,
    slug_hash,
    strip_accents,
)


app = FastAPI(title="Justice Pr\u00e1ska\u010d API")


@app.on_event("startup")
def on_startup():
    logger.info("Application starting")
    init_db()


_cors_origins_env = os.environ.get("JUSTICE_CORS_ORIGINS", "")
if _cors_origins_env:
    _cors_origins = [o.strip() for o in _cors_origins_env.split(",") if o.strip()]
    if _cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=_cors_origins,
            allow_methods=["GET", "OPTIONS"],
            allow_headers=["Accept", "Content-Type"],
        )

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "P\u0159\u00edli\u0161 mnoho po\u017eadavk\u016f. Zkuste to pozd\u011bji."},
        headers={"Retry-After": "60"},
    )


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/search")
@limiter.limit("30/minute")
def api_search(request: Request, q: str = Query(..., min_length=2)) -> dict[str, Any]:
    logger.info(f"api_search q={q}")
    results = search_companies(q)
    return {"query": q, "count": len(results), "results": results}


@app.get("/api/history")
def api_history(request: Request) -> dict[str, Any]:
    visitor_id = request.headers.get("X-Visitor-Id")
    logger.info(f"api_history visitor_id={visitor_id}")
    return {"items": get_history_entries(visitor_id)}


@app.get("/api/company")
@limiter.limit("10/minute")
def api_company(request: Request, subjekt_id: str = Query(..., alias="subjektId"), q: str | None = Query(None), refresh: bool = Query(False)) -> dict[str, Any]:
    subjekt_id = subjekt_id.strip()
    if not subjekt_id or not subjekt_id.isdigit():
        raise HTTPException(status_code=400, detail="Neplatn\u00e9 ID subjektu.")
    logger.info(f"api_company subjekt_id={subjekt_id} refresh={refresh}")
    visitor_id = request.headers.get("X-Visitor-Id")
    try:
        profile = build_company_profile(subjekt_id, visitor_id=visitor_id, query=q, force_refresh=refresh)
    except Exception as exc:
        logger.exception(f"api_company error subjekt_id={subjekt_id}")
        raise HTTPException(status_code=422, detail=public_error_message(exc)) from exc
    return profile


def inline_pdf_filename(label: str | None, index: int) -> str:
    raw = norm_text(label or f"listina-{index + 1}.pdf")
    safe = re.sub(r'[^A-Za-z0-9._-]+', '-', strip_accents(raw)).strip('-') or f"listina-{index + 1}.pdf"
    if not safe.lower().endswith('.pdf'):
        safe += '.pdf'
    return safe


@app.get("/api/document/resolve")
@limiter.limit("10/minute")
def api_document_resolve(request: Request, detail_url: str = Query(..., alias="detailUrl"), index: int = Query(0, ge=0), prefer_pdf: bool = Query(True)) -> FileResponse:
    logger.info(f"api_document_resolve detail_url={detail_url} index={index}")
    parsed_url = urlparse(detail_url)
    if parsed_url.scheme != "https" or parsed_url.hostname != "or.justice.cz":
        raise HTTPException(status_code=400, detail="Neplatn\u00e1 URL dokumentu.")
    detail = parse_document_detail(detail_url, force_refresh=True)
    downloads = detail.get("download_links") or []
    if prefer_pdf:
        downloads = [item for item in downloads if item.get("is_pdf")]
    if not downloads:
        raise HTTPException(status_code=404, detail="Pro tuto listinu se nepoda\u0159ilo naj\u00edt \u017e\u00e1dn\u00fd soubor.")
    if index >= len(downloads):
        raise HTTPException(status_code=404, detail="Po\u017eadovan\u00fd soubor na detailu listiny nen\u00ed k dispozici.")
    selected = downloads[index]
    pdf_path = fetch_binary(selected.get("url") or "", PDF_DIR / f"{slug_hash(selected.get('url') or '')}.pdf")
    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename=inline_pdf_filename(selected.get("label"), index),
        headers={"Content-Disposition": f'inline; filename="{inline_pdf_filename(selected.get("label"), index)}"'},
    )


@app.get("/api/company/stream")
@limiter.limit("10/minute")
def api_company_stream(request: Request, subjekt_id: str = Query(..., alias="subjektId"), q: str | None = Query(None), refresh: bool = Query(False)) -> StreamingResponse:
    subjekt_id = subjekt_id.strip()
    if not subjekt_id or not subjekt_id.isdigit():
        raise HTTPException(status_code=400, detail="Neplatn\u00e9 ID subjektu.")
    logger.info(f"api_company_stream subjekt_id={subjekt_id} refresh={refresh}")
    visitor_id = request.headers.get("X-Visitor-Id")

    def sse_event(event: str, payload: dict[str, Any]) -> str:
        return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    def iterator() -> Iterable[str]:
        try:
            cache_name = f"company_profile_{PROFILE_CACHE_VERSION}_{subjekt_id}"
            if not refresh:
                cached = load_json_cache(cache_name, PROFILE_CACHE_TTL_SECONDS)
                if cached is not None:
                    cached["cache_status"] = "cached"
                    save_history_entry(visitor_id, cached, query=q)
                    yield sse_event("status", {"label": "Na\u010d\u00edt\u00e1m ulo\u017een\u00fd profil z mezipam\u011bti"})
                    yield sse_event("result", cached)
                    return

            yield sse_event("status", {"label": "Spou\u0161t\u00edm novou extrakci z ve\u0159ejn\u00fdch podklad\u016f" if refresh else "Otev\u00edr\u00e1m aktu\u00e1ln\u00ed v\u00fdpis firmy"})
            current_extract = fetch_extract(subjekt_id, "PLATNY", force_refresh=refresh)
            basic_info_items = build_basic_info(current_extract)
            company_name = current_extract.get("basic_info", {}).get("Obchodn\u00ed firma") or current_extract.get("subtitle") or "Spole\u010dnost"
            ico = clean_ico(str(current_extract.get("basic_info", {}).get("Identifika\u010dn\u00ed \u010d\u00edslo", "")))
            yield sse_event("preview", {"subject_id": subjekt_id, "name": company_name, "ico": ico, "basic_info": basic_info_items})

            yield sse_event("status", {"label": "\u010ctu \u00fapln\u00fd v\u00fdpis a historii zm\u011bn"})
            full_extract = fetch_extract(subjekt_id, "UPLNY", force_refresh=refresh)
            people = extract_people_and_owners(current_extract)
            history = extract_history_events(full_extract)

            yield sse_event("status", {"label": "Stahuji seznam listin ze Sb\u00edrky listin"})
            docs = parse_document_list(subjekt_id, force_refresh=refresh)
            relevant_docs = pick_recent_financial_docs(docs, max_years=5, force_refresh_details=refresh)
            yield sse_event("status", {"label": f"Vybral jsem {len(relevant_docs)} relevantn\u00edch listin a projdu v\u0161echny kandid\u00e1tn\u00ed PDF p\u0159\u00edlohy"})

            timeline_map: dict[int, dict[str, Any]] = {}
            processed_docs: list[dict[str, Any]] = []
            total_docs = len(relevant_docs)
            for idx, doc in enumerate(relevant_docs, start=1):
                year_hint = (doc.get("years") or [None])[0]
                doc_title = doc.get("document_number") or doc.get("type") or "listina"
                candidate_count = len(doc.get("pdf_candidates") or [])
                if year_hint:
                    label = f"\u010ctu listinu {idx}/{total_docs}: {doc_title} \u00b7 rok {year_hint} \u00b7 soubory {candidate_count}"
                else:
                    label = f"\u010ctu listinu {idx}/{total_docs}: {doc_title} \u00b7 soubory {candidate_count}"
                yield sse_event("status", {"label": label})
                doc_copy, year_map = extract_financial_doc_data(doc)
                processed_docs.append(doc_copy)
                merge_doc_year_map(timeline_map, doc_copy, year_map)
                metric_count = len(doc_copy.get("metrics_found") or [])
                if metric_count:
                    yield sse_event("status", {"label": f"Z listiny {idx}/{total_docs} jsem vyt\u00e1hl {metric_count} metrik"})
                else:
                    yield sse_event("status", {"label": f"Listina {idx}/{total_docs} m\u00e1 slab\u0161\u00ed \u010ditelnost, zkou\u0161\u00edm dal\u0161\u00ed podklady"})

            timeline = finalize_financial_timeline(timeline_map)
            overview, deep, praskac = build_highlights(timeline, processed_docs, history)

            yield sse_event("status", {"label": "Kontroluji trendy, d\u00edry v letech a ve\u0159ejn\u00e9 sign\u00e1ly"})
            if AI_ENABLED:
                yield sse_event("status", {"label": "P\u00ed\u0161u AI shrnut\u00ed a skl\u00e1d\u00e1m body do profilu"})
                try:
                    ai_analysis = generate_ai_analysis(
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
                except Exception:
                    logger.exception("generate_ai_analysis failed, using fallback")
                    ai_analysis = {
                        "analysis_engine": "fallback",
                        "analysis_model": None,
                        "analysis_usage": None,
                        "analysis_overview": "Shrnut\u00ed b\u011b\u017e\u00ed bez AI vrstvy. N\u00ed\u017ee je pravidlov\u00fd v\u00fdstup z ve\u0159ejn\u00fdch podklad\u016f justice.cz.",
                        "data_quality_note": "Kvalita dat z\u00e1vis\u00ed na \u010ditelnosti ve\u0159ejn\u00fdch PDF a \u00faplnosti Sb\u00edrky listin.",
                        "insight_summary": overview,
                        "deep_insights": deep,
                        "praskac": praskac,
                    }
            else:
                ai_analysis = {
                    "analysis_engine": "disabled",
                    "analysis_model": None,
                    "analysis_usage": None,
                    "analysis_overview": "AI vrstva je vypnut\u00e1. N\u00ed\u017ee je pravidlov\u00fd v\u00fdstup z ve\u0159ejn\u00fdch podklad\u016f justice.cz.",
                    "data_quality_note": "Kvalita dat z\u00e1vis\u00ed na \u010ditelnosti ve\u0159ejn\u00fdch PDF a \u00faplnosti Sb\u00edrky listin.",
                    "insight_summary": overview,
                    "deep_insights": deep,
                    "praskac": praskac,
                }

            yield sse_event("status", {"label": "Porovn\u00e1v\u00e1m \u010d\u00edsla s ve\u0159ejnou kontrolou a ukl\u00e1d\u00e1m sd\u00edlenou historii"})
            external_checks = build_external_checks(timeline, company_name, ico)
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
                "generated_at": datetime.now().astimezone().isoformat(),
                "cache_status": "fresh",
            }
            save_json_cache(cache_name, profile)
            save_history_entry(visitor_id, profile, query=q)
            yield sse_event("result", profile)
        except Exception as exc:
            logger.exception(f"api_company_stream error subjekt_id={subjekt_id}")
            yield sse_event("error", {"detail": public_error_message(exc)})

    return StreamingResponse(iterator(), media_type="text/event-stream")


from fastapi.staticfiles import StaticFiles

_static_dir = Path(__file__).resolve().parent.parent

@app.get("/")
def serve_index():
    return FileResponse(_static_dir / "index.html")

app.mount("/", StaticFiles(directory=str(_static_dir)), name="static")
