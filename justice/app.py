from __future__ import annotations

import io
import json
import os
import re
import threading
from queue import Empty, Queue
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
    enhance_company_profile_with_ai,
)
from justice.db import get_document_by_detail, get_history_entries, get_history_profile, init_db, touch_recent_search
from justice.documents import parse_document_detail
from justice.pipeline import run_company_pipeline
from justice.scraping import fetch_binary_bytes, search_companies
from justice.storage_r2 import open_binary_stream
from justice.utils import (
    ROOT_DIR,
    logger,
    norm_text,
    public_error_message,
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
            allow_methods=["GET", "POST", "OPTIONS"],
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
def api_history(
    request: Request,
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    visitor_id = request.headers.get("X-Visitor-Id")
    logger.info(f"api_history visitor_id={visitor_id} limit={limit} offset={offset}")
    items, total = get_history_entries(visitor_id, limit=limit, offset=offset)
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(items) < total,
    }


@app.get("/api/company")
@limiter.limit("10/minute")
def api_company(request: Request, subjekt_id: str = Query(..., alias="subjektId"), q: str | None = Query(None), refresh: bool = Query(False)) -> dict[str, Any]:
    subjekt_id = subjekt_id.strip()
    if not subjekt_id or not subjekt_id.isdigit():
        raise HTTPException(status_code=400, detail="Neplatn\u00e9 ID subjektu.")
    logger.info(f"api_company subjekt_id={subjekt_id} refresh={refresh}")
    visitor_id = request.headers.get("X-Visitor-Id")
    try:
        profile = run_company_pipeline(subjekt_id, visitor_id=visitor_id, query=q, force_refresh=refresh)
    except Exception as exc:
        logger.exception(f"api_company error subjekt_id={subjekt_id}")
        raise HTTPException(status_code=422, detail=public_error_message(exc)) from exc
    return profile


def load_stored_company_profile(subjekt_id: str) -> dict[str, Any] | None:
    profile = get_history_profile(subjekt_id)
    if profile is not None:
        profile.setdefault("cache_status", "cached")
    return profile


@app.get("/api/company/stored")
@limiter.limit("20/minute")
def api_company_stored(request: Request, subjekt_id: str = Query(..., alias="subjektId"), q: str | None = Query(None)) -> dict[str, Any]:
    subjekt_id = subjekt_id.strip()
    if not subjekt_id or not subjekt_id.isdigit():
        raise HTTPException(status_code=400, detail="Neplatné ID subjektu.")
    logger.info(f"api_company_stored subjekt_id={subjekt_id}")
    visitor_id = request.headers.get("X-Visitor-Id")
    profile = load_stored_company_profile(subjekt_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profil firmy zatím není uložený.")
    touch_recent_search(
        subjekt_id,
        query=q,
        visitor_id=visitor_id,
        ico=str(profile.get("ico") or ""),
        name=str(profile.get("name") or subjekt_id),
    )
    return profile


@app.post("/api/company/ai")
@limiter.limit("10/minute")
def api_company_ai(request: Request, subjekt_id: str = Query(..., alias="subjektId"), q: str | None = Query(None)) -> dict[str, Any]:
    subjekt_id = subjekt_id.strip()
    if not subjekt_id or not subjekt_id.isdigit():
        raise HTTPException(status_code=400, detail="Neplatné ID subjektu.")
    logger.info(f"api_company_ai subjekt_id={subjekt_id}")
    visitor_id = request.headers.get("X-Visitor-Id")
    profile = load_stored_company_profile(subjekt_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profil firmy zatím není uložený. Nejdřív načti /api/company.")
    try:
        return enhance_company_profile_with_ai(profile, visitor_id=visitor_id, query=q)
    except Exception as exc:
        logger.exception(f"api_company_ai error subjekt_id={subjekt_id}")
        raise HTTPException(status_code=422, detail=public_error_message(exc)) from exc


def inline_pdf_filename(label: str | None, index: int) -> str:
    raw = norm_text(label or f"listina-{index + 1}.pdf")
    safe = re.sub(r'[^A-Za-z0-9._-]+', '-', strip_accents(raw)).strip('-') or f"listina-{index + 1}.pdf"
    if not safe.lower().endswith('.pdf'):
        safe += '.pdf'
    return safe


@app.get("/api/document/resolve")
@limiter.limit("10/minute")
def api_document_resolve(request: Request, detail_url: str = Query(..., alias="detailUrl"), index: int = Query(0, ge=0), prefer_pdf: bool = Query(True)):
    logger.info(f"api_document_resolve detail_url={detail_url} index={index}")
    parsed_url = urlparse(detail_url)
    if parsed_url.scheme != "https" or parsed_url.hostname != "or.justice.cz":
        raise HTTPException(status_code=400, detail="Neplatn\u00e1 URL dokumentu.")
    stored = get_document_by_detail(detail_url, index)
    if stored and stored.get("r2_pdf_key"):
        try:
            safe_name = inline_pdf_filename(stored.get("document_number") or stored.get("doc_type"), index)
            return StreamingResponse(
                open_binary_stream(str(stored["r2_pdf_key"])),
                media_type="application/pdf",
                headers={"Content-Disposition": f'inline; filename="{safe_name}"'},
            )
        except Exception:
            logger.exception(f"api_document_resolve stored fetch failed detail_url={detail_url} index={index}")
    detail = parse_document_detail(detail_url, force_refresh=True)
    downloads = detail.get("download_links") or []
    if prefer_pdf:
        downloads = [item for item in downloads if item.get("is_pdf")]
    if not downloads:
        raise HTTPException(status_code=404, detail="Pro tuto listinu se nepoda\u0159ilo naj\u00edt \u017e\u00e1dn\u00fd soubor.")
    if index >= len(downloads):
        raise HTTPException(status_code=404, detail="Po\u017eadovan\u00fd soubor na detailu listiny nen\u00ed k dispozici.")
    selected = downloads[index]
    pdf_bytes = fetch_binary_bytes(selected.get("url") or "")
    safe_name = inline_pdf_filename(selected.get("label"), index)
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{safe_name}"'},
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
        queue: Queue[str | None] = Queue()

        def on_progress(event: str, payload: dict[str, Any]) -> None:
            queue.put(sse_event(event, payload))

        def worker() -> None:
            try:
                profile = run_company_pipeline(
                    subjekt_id,
                    visitor_id=visitor_id,
                    query=q,
                    force_refresh=refresh,
                    on_progress=on_progress,
                )
                queue.put(sse_event("result", profile))
            except Exception as exc:
                logger.exception(f"api_company_stream error subjekt_id={subjekt_id}")
                queue.put(sse_event("error", {"detail": public_error_message(exc)}))
            finally:
                queue.put(None)

        threading.Thread(target=worker, daemon=True).start()
        yield ": stream-open\n\n"
        while True:
            try:
                chunk = queue.get(timeout=15)
            except Empty:
                yield ": keep-alive\n\n"
                continue
            if chunk is None:
                break
            yield chunk

    return StreamingResponse(
        iterator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


_STATIC_FILES = {"app.js", "style.css", "base.css", "praskac-icon.png"}


@app.get("/")
def serve_index():
    return FileResponse(ROOT_DIR / "index.html")


@app.get("/firma/{subjekt_id}")
def serve_company_page(subjekt_id: str):
    return FileResponse(ROOT_DIR / "index.html")


@app.get("/{filename}")
def serve_static(filename: str):
    if filename not in _STATIC_FILES:
        raise HTTPException(status_code=404)
    return FileResponse(ROOT_DIR / filename)
