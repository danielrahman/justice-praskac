from pathlib import Path

server_path = Path('/home/user/workspace/justice-praskac/server.py')
app_path = Path('/home/user/workspace/justice-praskac/app.js')
style_path = Path('/home/user/workspace/justice-praskac/style.css')

server = server_path.read_text(encoding='utf-8')
app = app_path.read_text(encoding='utf-8')
style = style_path.read_text(encoding='utf-8')

server = server.replace(
    'from fastapi.responses import RedirectResponse, StreamingResponse\n',
    'from fastapi.responses import FileResponse, StreamingResponse\n'
)

server = server.replace(
    'PROFILE_CACHE_VERSION = "v6_shared_history_mobile_status"\nOCR_CACHE_VERSION = "v2_statement_refresh"\n',
    'PROFILE_CACHE_VERSION = "v7_all_attachments_redesign"\nOCR_CACHE_VERSION = "v3_all_attachments_refresh"\n'
)

old = '''def parse_document_detail(url: str, force_refresh: bool = False) -> dict[str, Any]:
    cache_name = f"doc_detail_{slug_hash(url)}"
    if not force_refresh:
        cached = load_json_cache(cache_name, 60 * 60 * 24 * 7)
        if cached is not None:
            return cached
    html = fetch_text(url)
    soup = BeautifulSoup(html, "html.parser")
    downloads: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for link in soup.find_all("a", href=re.compile(r"/ias/content/download\\?id=")):
        download_url = urljoin(BASE_SITE, link["href"])
        if download_url in seen_urls:
            continue
        seen_urls.add(download_url)
        label = norm_text(link.get_text(" ", strip=True))
        is_pdf = ".pdf" in label.lower()
        downloads.append(
            {
                "label": label,
                "url": download_url,
                "is_pdf": is_pdf,
            }
        )
    pdf_candidates = [item for item in downloads if item.get("is_pdf")]
    pdf_url = pdf_candidates[0]["url"] if pdf_candidates else None
    pdf_name = pdf_candidates[0]["label"] if pdf_candidates else None
    result = {
        "detail_url": url,
        "pdf_url": pdf_url,
        "pdf_name": pdf_name,
        "download_links": downloads,
    }
    save_json_cache(cache_name, result)
    return result
'''
new = '''def extract_attachment_page_hint(label: str | None) -> int | None:
    if not label:
        return None
    match = re.search(r"počet stran:\s*(\\d+)", label, flags=re.I)
    return int(match.group(1)) if match else None


def extract_attachment_size_kb(label: str | None) -> float | None:
    if not label:
        return None
    match = re.search(r"(\\d+(?:[.,]\\d+)?)\\s*kB", label, flags=re.I)
    if not match:
        return None
    return float(match.group(1).replace(",", "."))


def financial_attachment_score(label: str | None, page_hint: int | None = None, parent_type: str | None = None) -> int:
    key = norm_key(label or "")
    parent_key = norm_key(parent_type or "")
    score = 0
    if "uz-" in key:
        score += 130
    if "rozvaha" in key:
        score += 110
    if "vykaz zisku" in key or "zisku a ztraty" in key:
        score += 110
    if "ucetni zaverka" in key and "priloha" not in key:
        score += 70
    if "vyrocni zprava" in key:
        score += 55
    if "audit" in key:
        score += 20
    if "priloha" in key:
        score -= 45
    if "opis prilohy" in key:
        score -= 110
    if ".xml" in key:
        score -= 160
    if parent_key and "ucetni zaverka" in parent_key:
        score += 10
    if page_hint is not None:
        score += min(page_hint, 40)
        if "uz-" in key and page_hint <= 3:
            score += 45
        if page_hint == 1 and "priloha" in key:
            score -= 30
    size_kb = extract_attachment_size_kb(label)
    if size_kb is not None:
        score += min(int(size_kb // 40), 20)
    return score


def build_pdf_candidates(downloads: list[dict[str, Any]], parent_type: str | None = None) -> list[dict[str, Any]]:
    pdf_candidates: list[dict[str, Any]] = []
    pdf_index = 0
    for item in downloads:
        if not item.get("is_pdf"):
            continue
        label = str(item.get("label") or "")
        page_hint = extract_attachment_page_hint(label)
        candidate = dict(item)
        candidate["page_hint"] = page_hint
        candidate["candidate_score"] = financial_attachment_score(label, page_hint, parent_type)
        candidate["pdf_index"] = pdf_index
        pdf_candidates.append(candidate)
        pdf_index += 1
    pdf_candidates.sort(
        key=lambda item: (
            item.get("candidate_score") or 0,
            item.get("page_hint") or 0,
            len(item.get("label") or ""),
        ),
        reverse=True,
    )
    return pdf_candidates


def parse_document_detail(url: str, force_refresh: bool = False, parent_type: str | None = None) -> dict[str, Any]:
    cache_name = f"doc_detail_{slug_hash(url)}"
    if not force_refresh:
        cached = load_json_cache(cache_name, 60 * 60 * 24 * 7)
        if cached is not None:
            if "pdf_candidates" not in cached:
                cached["pdf_candidates"] = build_pdf_candidates(cached.get("download_links") or [], parent_type)
                if cached["pdf_candidates"]:
                    cached["pdf_url"] = cached["pdf_candidates"][0]["url"]
                    cached["pdf_name"] = cached["pdf_candidates"][0]["label"]
            return cached
    html = fetch_text(url)
    soup = BeautifulSoup(html, "html.parser")
    downloads: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for link in soup.find_all("a", href=re.compile(r"/ias/content/download\\?id=")):
        download_url = urljoin(BASE_SITE, link["href"])
        if download_url in seen_urls:
            continue
        seen_urls.add(download_url)
        label = norm_text(link.get_text(" ", strip=True))
        is_pdf = ".pdf" in label.lower()
        downloads.append(
            {
                "label": label,
                "url": download_url,
                "is_pdf": is_pdf,
            }
        )
    pdf_candidates = build_pdf_candidates(downloads, parent_type)
    pdf_url = pdf_candidates[0]["url"] if pdf_candidates else None
    pdf_name = pdf_candidates[0]["label"] if pdf_candidates else None
    result = {
        "detail_url": url,
        "pdf_url": pdf_url,
        "pdf_name": pdf_name,
        "pdf_candidates": pdf_candidates,
        "download_links": downloads,
    }
    save_json_cache(cache_name, result)
    return result
'''
if old not in server:
    raise SystemExit('parse_document_detail block not found')
server = server.replace(old, new)

old = '''def pick_recent_financial_docs(docs: list[dict[str, Any]], max_years: int = 5, force_refresh_details: bool = False) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    covered_years: list[int] = []
    candidates = [doc for doc in docs if is_financial_document(doc)]
    sorted_docs = sorted(
        candidates,
        key=lambda d: (
            (d.get("years") or [0])[0],
            financial_doc_score(d),
            d.get("filed_date") or "",
            d.get("created_date") or "",
        ),
        reverse=True,
    )
    for doc in sorted_docs:
        doc_years = doc.get("years") or []
        primary_year = doc_years[0] if doc_years else None
        if primary_year is None:
            continue
        if primary_year not in covered_years and len(covered_years) >= max_years:
            continue
        same_year_count = sum(1 for item in selected if ((item.get("years") or [None])[0] == primary_year))
        if same_year_count >= 2:
            continue
        enriched = dict(doc)
        enriched.update(parse_document_detail(doc["detail_url"], force_refresh=force_refresh_details))
        enriched["doc_quality_score"] = financial_doc_score(enriched)
        selected.append(enriched)
        if primary_year not in covered_years:
            covered_years.append(primary_year)
    selected.sort(
        key=lambda d: (
            (d.get("years") or [0])[0],
            d.get("doc_quality_score") or 0,
            d.get("filed_date") or "",
        ),
        reverse=True,
    )
    return selected
'''
new = '''def pick_recent_financial_docs(docs: list[dict[str, Any]], max_years: int = 5, force_refresh_details: bool = False) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    covered_years: list[int] = []
    candidates = [doc for doc in docs if is_financial_document(doc)]
    sorted_docs = sorted(
        candidates,
        key=lambda d: (
            (d.get("years") or [0])[0],
            financial_doc_score(d),
            d.get("filed_date") or "",
            d.get("created_date") or "",
        ),
        reverse=True,
    )
    for doc in sorted_docs:
        doc_years = doc.get("years") or []
        primary_year = doc_years[0] if doc_years else None
        if primary_year is None:
            continue
        if primary_year not in covered_years and len(covered_years) >= max_years:
            continue
        enriched = dict(doc)
        enriched.update(parse_document_detail(doc["detail_url"], force_refresh=force_refresh_details, parent_type=doc.get("type")))
        enriched["doc_quality_score"] = financial_doc_score(enriched)
        enriched["candidate_file_count"] = len(enriched.get("pdf_candidates") or [])
        selected.append(enriched)
        if primary_year not in covered_years:
            covered_years.append(primary_year)
    selected.sort(
        key=lambda d: (
            (d.get("years") or [0])[0],
            d.get("doc_quality_score") or 0,
            d.get("candidate_file_count") or 0,
            d.get("filed_date") or "",
        ),
        reverse=True,
    )
    return selected
'''
if old not in server:
    raise SystemExit('pick_recent_financial_docs block not found')
server = server.replace(old, new)

old = '''def get_pdf_text(pdf_url: str) -> dict[str, Any]:
    pdf_id = slug_hash(pdf_url)
    pdf_path = PDF_DIR / f"{pdf_id}.pdf"
    text_path = TEXT_DIR / f"{pdf_id}.txt"
    ocr_path = TEXT_DIR / f"{pdf_id}.{OCR_CACHE_VERSION}.ocr.txt"
    fetch_binary(pdf_url, pdf_path)
    digital = extract_text_digital(pdf_path, text_path)
    useful_len = len(re.sub(r"\\s+", "", digital))
    page_count = pdf_page_count(pdf_path)
    ocr = ocr_selected_pages(pdf_path, ocr_path)
    digital_key = norm_key(digital)
    ocr_key = norm_key(ocr)
    if useful_len >= 2500 and not any(token in ocr_key for token in ["aktiva celkem", "vykaz zisku a ztraty", "trzby z prodeje"]):
        return {
            "text": digital,
            "digital_text": digital,
            "ocr_text": None,
            "mode": "digital",
            "page_count": page_count,
            "pdf_path": str(pdf_path),
        }
    preferred_text = ocr or digital
    mode = "ocr" if ocr else "digital"
    if any(token in ocr_key for token in ["aktiva celkem", "vykaz zisku a ztraty", "trzby z prodeje"]):
        preferred_text = ocr
        mode = "ocr"
    elif useful_len >= 2500 and any(token in digital_key for token in ["aktiva celkem", "vykaz zisku a ztraty", "trzby z prodeje"]):
        preferred_text = digital
        mode = "digital"
    return {
        "text": preferred_text,
        "digital_text": digital or None,
        "ocr_text": ocr or None,
        "mode": mode,
        "page_count": page_count,
        "pdf_path": str(pdf_path),
    }
'''
new = '''def get_pdf_text(pdf_url: str) -> dict[str, Any]:
    pdf_id = slug_hash(pdf_url)
    pdf_path = PDF_DIR / f"{pdf_id}.pdf"
    text_path = TEXT_DIR / f"{pdf_id}.txt"
    ocr_path = TEXT_DIR / f"{pdf_id}.{OCR_CACHE_VERSION}.ocr.txt"
    fetch_binary(pdf_url, pdf_path)
    digital = extract_text_digital(pdf_path, text_path)
    useful_len = len(re.sub(r"\\s+", "", digital))
    page_count = pdf_page_count(pdf_path)
    ocr = ocr_selected_pages(pdf_path, ocr_path)
    digital_key = norm_key(digital)
    ocr_key = norm_key(ocr)
    if useful_len >= 2500 and not any(token in ocr_key for token in ["aktiva celkem", "vykaz zisku a ztraty", "trzby z prodeje"]):
        return {
            "text": digital,
            "digital_text": digital,
            "ocr_text": ocr or None,
            "mode": "digital",
            "page_count": page_count,
            "pdf_path": str(pdf_path),
        }
    preferred_text = ocr or digital
    mode = "ocr" if ocr else "digital"
    if any(token in ocr_key for token in ["aktiva celkem", "vykaz zisku a ztraty", "trzby z prodeje"]):
        preferred_text = ocr
        mode = "ocr"
    elif useful_len >= 2500 and any(token in digital_key for token in ["aktiva celkem", "vykaz zisku a ztraty", "trzby z prodeje"]):
        preferred_text = digital
        mode = "digital"
    return {
        "text": preferred_text,
        "digital_text": digital or None,
        "ocr_text": ocr or None,
        "mode": mode,
        "page_count": page_count,
        "pdf_path": str(pdf_path),
    }


def build_metric_source_text(pdf_text: dict[str, Any]) -> str:
    pieces: list[str] = []
    seen: set[str] = set()
    for key in ("digital_text", "ocr_text", "text"):
        value = str(pdf_text.get(key) or "").strip()
        compact = re.sub(r"\\s+", "", value)
        if not value or len(compact) < 40:
            continue
        if value in seen:
            continue
        seen.add(value)
        pieces.append(value)
    return "\\n\\n".join(pieces)
'''
if old not in server:
    raise SystemExit('get_pdf_text block not found')
server = server.replace(old, new)

old = '''def extract_financial_doc_data(doc: dict[str, Any]) -> tuple[dict[str, Any], dict[int, dict[str, float]]]:
    doc_copy = dict(doc)
    if not doc.get("pdf_url"):
        doc_copy["extraction_mode"] = "missing"
        doc_copy["page_count"] = doc.get("pages", 0)
        doc_copy["metrics_found"] = []
        doc_copy["download_links"] = doc.get("download_links") or []
        return doc_copy, {}

    primary_year = (doc.get("years") or [None])[0]
    if not primary_year:
        doc_copy["extraction_mode"] = "unknown"
        doc_copy["page_count"] = doc.get("pages", 0)
        doc_copy["metrics_found"] = []
        doc_copy["download_links"] = doc.get("download_links") or []
        return doc_copy, {}

    try:
        pdf_text = get_pdf_text(doc["pdf_url"])
        extracted = extract_financial_metrics_from_text(pdf_text["text"], primary_year)
    except Exception:
        extracted = {"year_map": {}, "multiplier": 1000, "found_metrics": {}}
        pdf_text = {"mode": "unknown", "page_count": doc.get("pages", 0)}

    doc_copy["extraction_mode"] = pdf_text.get("mode")
    doc_copy["page_count"] = pdf_text.get("page_count")
    doc_copy["metrics_found"] = sorted(list(extracted.get("found_metrics", {}).keys()))
    doc_copy["download_links"] = doc.get("download_links") or []
    return doc_copy, extracted.get("year_map", {})
'''
new = '''def merge_attachment_year_map(target: dict[int, dict[str, Any]], year_map: dict[int, dict[str, float]], weight: int, primary_year: int | None) -> None:
    for year, values in year_map.items():
        slot = target.setdefault(year, {})
        value_score = weight + 20 if primary_year and year == primary_year else weight - 20
        for key, value in values.items():
            if value is None:
                continue
            existing_score = slot.get(f"_{key}_score", -10**9)
            if value_score > existing_score:
                slot[key] = value
                slot[f"_{key}_score"] = value_score


def extract_financial_doc_data(doc: dict[str, Any]) -> tuple[dict[str, Any], dict[int, dict[str, float]]]:
    doc_copy = dict(doc)
    attachments = list(doc.get("pdf_candidates") or [])
    if not attachments and doc.get("pdf_url"):
        attachments = [{
            "label": doc.get("pdf_name") or "PDF",
            "url": doc.get("pdf_url"),
            "is_pdf": True,
            "candidate_score": 0,
            "page_hint": doc.get("pages"),
            "pdf_index": 0,
        }]
    if not attachments:
        doc_copy["extraction_mode"] = "missing"
        doc_copy["page_count"] = doc.get("pages", 0)
        doc_copy["metrics_found"] = []
        doc_copy["combined_metrics_found"] = []
        doc_copy["download_links"] = doc.get("download_links") or []
        doc_copy["candidate_files"] = []
        doc_copy["candidate_file_count"] = 0
        doc_copy["extraction_scope"] = "all_candidate_files"
        return doc_copy, {}

    primary_year = (doc.get("years") or [None])[0]
    if not primary_year:
        doc_copy["extraction_mode"] = "unknown"
        doc_copy["page_count"] = doc.get("pages", 0)
        doc_copy["metrics_found"] = []
        doc_copy["combined_metrics_found"] = []
        doc_copy["download_links"] = doc.get("download_links") or []
        doc_copy["candidate_files"] = []
        doc_copy["candidate_file_count"] = len(attachments)
        doc_copy["extraction_scope"] = "all_candidate_files"
        return doc_copy, {}

    attachment_results: list[dict[str, Any]] = []
    merged_year_map: dict[int, dict[str, Any]] = {}
    combined_parts: list[str] = []
    modes: set[str] = set()
    doc_metrics: set[str] = set()
    total_pages = 0
    best_weight = int(doc_copy.get("doc_quality_score") or 0)

    for attachment in attachments:
        attachment_copy = {
            "label": attachment.get("label"),
            "url": attachment.get("url"),
            "pdf_index": attachment.get("pdf_index"),
            "page_hint": attachment.get("page_hint"),
            "candidate_score": attachment.get("candidate_score") or 0,
        }
        try:
            pdf_text = get_pdf_text(str(attachment.get("url") or ""))
            metric_text = build_metric_source_text(pdf_text)
            extracted = extract_financial_metrics_from_text(metric_text, primary_year) if metric_text.strip() else {"year_map": {}, "found_metrics": {}}
            found_metrics = sorted(list((extracted.get("found_metrics") or {}).keys()))
            attachment_copy["page_count"] = pdf_text.get("page_count") or attachment.get("page_hint") or 0
            attachment_copy["extraction_mode"] = pdf_text.get("mode")
            attachment_copy["metrics_found"] = found_metrics
            if metric_text.strip():
                combined_parts.append(f"\\n\\n--- ATTACHMENT {attachment_copy.get('label') or 'PDF'} ---\\n{metric_text[:120000]}")
            weight = int(doc_copy.get("doc_quality_score") or 0) + int(attachment.get("candidate_score") or 0)
            best_weight = max(best_weight, weight)
            merge_attachment_year_map(merged_year_map, extracted.get("year_map") or {}, weight, primary_year)
            doc_metrics.update(found_metrics)
            if attachment_copy.get("extraction_mode"):
                modes.add(str(attachment_copy.get("extraction_mode")))
            total_pages += int(attachment_copy.get("page_count") or 0)
        except Exception as exc:
            attachment_copy["page_count"] = attachment.get("page_hint") or 0
            attachment_copy["extraction_mode"] = "failed"
            attachment_copy["metrics_found"] = []
            attachment_copy["error"] = public_error_message(exc)
        attachment_results.append(attachment_copy)

    combined_metrics: list[str] = []
    if len(combined_parts) > 1:
        combined_extracted = extract_financial_metrics_from_text("".join(combined_parts), primary_year)
        combined_metrics = sorted(list((combined_extracted.get("found_metrics") or {}).keys()))
        merge_attachment_year_map(merged_year_map, combined_extracted.get("year_map") or {}, best_weight + 15, primary_year)
        doc_metrics.update(combined_metrics)

    final_year_map: dict[int, dict[str, float]] = {}
    for year, values in merged_year_map.items():
        final_year_map[year] = {key: value for key, value in values.items() if not key.startswith("_")}

    doc_copy["download_links"] = doc.get("download_links") or []
    doc_copy["candidate_files"] = attachment_results
    doc_copy["candidate_file_count"] = len(attachments)
    doc_copy["combined_metrics_found"] = combined_metrics
    doc_copy["metrics_found"] = sorted(doc_metrics)
    doc_copy["page_count"] = total_pages or doc.get("pages", 0)
    doc_copy["extraction_scope"] = "all_candidate_files"
    if not attachment_results:
        doc_copy["extraction_mode"] = "missing"
    elif len(modes) == 1:
        doc_copy["extraction_mode"] = next(iter(modes))
    elif modes:
        doc_copy["extraction_mode"] = "mixed"
    else:
        doc_copy["extraction_mode"] = "unknown"
    if attachment_results:
        best_attachment = max(attachment_results, key=lambda item: (item.get("candidate_score") or 0, item.get("page_count") or 0))
        doc_copy["pdf_url"] = best_attachment.get("url")
        doc_copy["pdf_name"] = best_attachment.get("label")
    return doc_copy, final_year_map
'''
if old not in server:
    raise SystemExit('extract_financial_doc_data block not found')
server = server.replace(old, new)

old = '''def compact_docs_for_ai(docs: list[dict[str, Any]], limit: int = 6) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for doc in docs[:limit]:
        compact.append({
            "document_number": doc.get("document_number"),
            "type": doc.get("type"),
            "years": doc.get("years"),
            "received_date": doc.get("received_date"),
            "filed_date": doc.get("filed_date"),
            "pages": doc.get("pages"),
            "page_count": doc.get("page_count"),
            "metrics_found": doc.get("metrics_found"),
            "extraction_mode": doc.get("extraction_mode"),
            "detail_url": doc.get("detail_url"),
            "pdf_url": doc.get("pdf_url"),
        })
    return compact
'''
new = '''def compact_docs_for_ai(docs: list[dict[str, Any]], limit: int = 6) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for doc in docs[:limit]:
        compact.append({
            "document_number": doc.get("document_number"),
            "type": doc.get("type"),
            "years": doc.get("years"),
            "received_date": doc.get("received_date"),
            "filed_date": doc.get("filed_date"),
            "pages": doc.get("pages"),
            "page_count": doc.get("page_count"),
            "candidate_file_count": doc.get("candidate_file_count"),
            "metrics_found": doc.get("metrics_found"),
            "combined_metrics_found": doc.get("combined_metrics_found"),
            "extraction_mode": doc.get("extraction_mode"),
            "detail_url": doc.get("detail_url"),
            "pdf_url": doc.get("pdf_url"),
            "candidate_files": [
                {
                    "label": item.get("label"),
                    "page_count": item.get("page_count"),
                    "extraction_mode": item.get("extraction_mode"),
                    "metrics_found": item.get("metrics_found"),
                }
                for item in (doc.get("candidate_files") or [])[:4]
            ],
        })
    return compact
'''
if old not in server:
    raise SystemExit('compact_docs_for_ai block not found')
server = server.replace(old, new)

server = server.replace(
    '            yield sse_event("status", {"label": f"Vybral jsem {len(relevant_docs)} relevantních finančních listin"})\n',
    '            yield sse_event("status", {"label": f"Vybral jsem {len(relevant_docs)} relevantních listin a projdu všechny kandidátní PDF přílohy"})\n'
)
server = server.replace(
    '                if year_hint:\n                    label = f"Čtu listinu {idx}/{total_docs}: {doc_title} · rok {year_hint}"\n                else:\n                    label = f"Čtu listinu {idx}/{total_docs}: {doc_title}"\n',
    '                candidate_count = len(doc.get("pdf_candidates") or [])\n                if year_hint:\n                    label = f"Čtu listinu {idx}/{total_docs}: {doc_title} · rok {year_hint} · soubory {candidate_count}"\n                else:\n                    label = f"Čtu listinu {idx}/{total_docs}: {doc_title} · soubory {candidate_count}"\n'
)

old = '''@app.get("/api/document/resolve")
def api_document_resolve(detail_url: str = Query(..., alias="detailUrl"), index: int = Query(0, ge=0), prefer_pdf: bool = Query(True)) -> RedirectResponse:
    detail = parse_document_detail(detail_url, force_refresh=True)
    downloads = detail.get("download_links") or []
    if prefer_pdf:
        downloads = [item for item in downloads if item.get("is_pdf")]
    if not downloads:
        raise HTTPException(status_code=404, detail="Pro tuto listinu se nepodařilo najít žádný soubor.")
    if index >= len(downloads):
        raise HTTPException(status_code=404, detail="Požadovaný soubor na detailu listiny není k dispozici.")
    resolved = resolve_live_download_url(downloads[index].get("url") or "")
    if not resolved:
        raise HTTPException(status_code=404, detail="Soubor se na detailu listiny momentálně nepodařilo otevřít.")
    return RedirectResponse(url=resolved, status_code=307)
'''
new = '''def inline_pdf_filename(label: str | None, index: int) -> str:
    raw = norm_text(label or f"listina-{index + 1}.pdf")
    safe = re.sub(r'[^A-Za-z0-9._-]+', '-', strip_accents(raw)).strip('-') or f"listina-{index + 1}.pdf"
    if not safe.lower().endswith('.pdf'):
        safe += '.pdf'
    return safe


@app.get("/api/document/resolve")
def api_document_resolve(detail_url: str = Query(..., alias="detailUrl"), index: int = Query(0, ge=0), prefer_pdf: bool = Query(True)) -> FileResponse:
    detail = parse_document_detail(detail_url, force_refresh=True)
    downloads = detail.get("download_links") or []
    if prefer_pdf:
        downloads = [item for item in downloads if item.get("is_pdf")]
    if not downloads:
        raise HTTPException(status_code=404, detail="Pro tuto listinu se nepodařilo najít žádný soubor.")
    if index >= len(downloads):
        raise HTTPException(status_code=404, detail="Požadovaný soubor na detailu listiny není k dispozici.")
    selected = downloads[index]
    pdf_path = fetch_binary(selected.get("url") or "", PDF_DIR / f"{slug_hash(selected.get('url') or '')}.pdf")
    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename=inline_pdf_filename(selected.get("label"), index),
        headers={"Content-Disposition": f'inline; filename="{inline_pdf_filename(selected.get("label"), index)}"'},
    )
'''
if old not in server:
    raise SystemExit('api_document_resolve block not found')
server = server.replace(old, new)

app_insert_after = '''function summaryListView(items) {
  if (!(items || []).length) {
    return '<div class="info-row"><strong>Shrnutí</strong><span>Z veřejných podkladů zatím nevyšlo dost spolehlivých bodů.</span></div>';
  }
  return (items || []).map((item) => insightRow(item, "insight-row")).join("");
}
'''
app_insert_new = '''function summaryListView(items) {
  if (!(items || []).length) {
    return '<div class="info-row"><strong>Shrnutí</strong><span>Z veřejných podkladů zatím nevyšlo dost spolehlivých bodů.</span></div>';
  }
  return (items || []).map((item) => insightRow(item, "insight-row")).join("");
}

function metricLabel(metric) {
  return {
    revenue: "tržby",
    operating_profit: "provozní výsledek",
    net_profit: "čistý výsledek",
    assets: "aktiva",
    equity: "vlastní kapitál",
    liabilities: "cizí zdroje",
    debt: "dluh",
  }[metric] || metric;
}

function metricPills(metrics) {
  if (!(metrics || []).length) {
    return '<span class="mini-pill mini-pill-muted">bez jistých metrik</span>';
  }
  return metrics.map((metric) => `<span class="mini-pill">${escapeHtml(metricLabel(metric))}</span>`).join("");
}

function renderDocumentCard(doc) {
  const files = doc.candidate_files || [];
  const primaryYear = (doc.years || ["?"])[0];
  return `
    <article class="doc-card">
      <div class="doc-card-head">
        <div>
          <strong>${escapeHtml(doc.document_number || "Listina")}</strong>
          <div class="doc-subtitle">${escapeHtml(doc.type || "")}</div>
        </div>
        <div class="doc-head-tags">
          <span class="tag tag-muted">rok ${escapeHtml(primaryYear)}</span>
          <span class="tag tag-muted">${escapeHtml(doc.extraction_mode || "?")}</span>
          <span class="tag tag-muted">${escapeHtml(String(doc.candidate_file_count || files.length || 0))} PDF</span>
        </div>
      </div>
      <div class="doc-summary-grid">
        <div class="info-row compact-row">
          <strong>Pokrytí</strong>
          <span>Procházím všechny kandidátní PDF přílohy k této listině, ne jen první soubor.</span>
        </div>
        <div class="info-row compact-row">
          <strong>Nalezené metriky</strong>
          <span class="mini-pill-row">${metricPills(doc.metrics_found || [])}</span>
        </div>
      </div>
      <div class="attachment-list">
        ${files.length
          ? files.map((file) => {
              const openUrl = `${API}/api/document/resolve?detailUrl=${encodeURIComponent(doc.detail_url || "")}&index=${encodeURIComponent(file.pdf_index ?? 0)}&prefer_pdf=true`;
              return `
                <div class="attachment-row">
                  <div>
                    <strong>${escapeHtml(file.label || "PDF příloha")}</strong>
                    <div class="attachment-meta">${escapeHtml(file.extraction_mode || "?")} · ${escapeHtml(String(file.page_count || file.page_hint || "?"))} stran</div>
                  </div>
                  <div class="attachment-actions">
                    <span class="mini-pill-row">${metricPills(file.metrics_found || [])}</span>
                    <a class="source-link" href="${escapeHtml(openUrl)}" target="_blank" rel="noopener noreferrer">otevřít PDF</a>
                  </div>
                </div>`;
            }).join("")
          : `<div class="attachment-row empty-attachment"><span>Nebyla nalezena žádná PDF příloha.</span></div>`}
      </div>
      <div class="doc-footer-links">
        <a class="source-link" href="${escapeHtml(doc.detail_url || "#")}" target="_blank" rel="noopener noreferrer">detail listiny</a>
      </div>
    </article>`;
}
'''
if app_insert_after not in app:
    raise SystemExit('summaryListView block not found')
app = app.replace(app_insert_after, app_insert_new)

old = '''      <div class="section-grid">
        <article class="card">
          <h3>Vlastníci a orgány</h3>
          <div class="list-grid">
            ${profile.owners?.length
              ? profile.owners.map((owner) => `
                <div class="person-row">
                  <strong>${escapeHtml(owner.role || "Vlastnická položka")}</strong>
                  <span>${escapeHtml(owner.raw || "")}</span>
                </div>`).join("")
              : (profile.statutory_bodies || []).slice(0, 4).map((body) => `
                <div class="person-row">
                  <strong>${escapeHtml(body.title)}</strong>
                  <span>${escapeHtml((body.items || []).length)} položek ve veřejném výpisu</span>
                </div>`).join("") || `<div class="empty-state" style="padding: 16px;"><p>Vlastnické údaje nejsou v tomto výpisu jasně rozepsané.</p></div>`}
          </div>
        </article>

        <article class="card">
          <h3>Relevantní listiny</h3>
          <div class="list-grid">
            ${(profile.financial_documents || []).map((doc) => `
              <div class="doc-row">
                <strong>${escapeHtml(doc.document_number || "Listina")}</strong>
                <span>${escapeHtml(doc.type || "")}</span>
                <span>Rok ${escapeHtml((doc.years || ["?"])[0])} · ${escapeHtml(doc.extraction_mode || "?")} · ${escapeHtml(String(doc.page_count || doc.pages || "?"))} stran</span>
                <span>
                  ${documentLinks(doc)}
                </span>
              </div>`).join("")}
          </div>
        </article>
      </div>
'''
new = '''      <div class="section-grid section-grid-docs">
        <article class="card">
          <h3>Vlastníci a orgány</h3>
          <div class="list-grid">
            ${profile.owners?.length
              ? profile.owners.map((owner) => `
                <div class="person-row">
                  <strong>${escapeHtml(owner.role || "Vlastnická položka")}</strong>
                  <span>${escapeHtml(owner.raw || "")}</span>
                </div>`).join("")
              : (profile.statutory_bodies || []).slice(0, 4).map((body) => `
                <div class="person-row">
                  <strong>${escapeHtml(body.title)}</strong>
                  <span>${escapeHtml((body.items || []).length)} položek ve veřejném výpisu</span>
                </div>`).join("") || `<div class="empty-state" style="padding: 16px;"><p>Vlastnické údaje nejsou v tomto výpisu jasně rozepsané.</p></div>`}
          </div>
        </article>

        <article class="card docs-section-card">
          <div class="docs-section-head">
            <div>
              <h3>Relevantní listiny</h3>
              <div class="small-note">U každé listiny zobrazím všechny kandidátní PDF přílohy a co se z nich podařilo vytáhnout.</div>
            </div>
            <div class="tag-stack">
              <span class="tag tag-muted">${escapeHtml(String((profile.financial_documents || []).length))} listin</span>
              <span class="tag tag-muted">všechny PDF přílohy</span>
            </div>
          </div>
          <div class="documents-grid">
            ${(profile.financial_documents || []).map((doc) => renderDocumentCard(doc)).join("")}
          </div>
        </article>
      </div>
'''
if old not in app:
    raise SystemExit('relevant docs section not found')
app = app.replace(old, new)

style_add_after = '.summary-note {\n  color: var(--text);\n}\n'
style_add = '''.summary-note {
  color: var(--text);
}

.section-grid-docs {
  align-items: start;
}

.docs-section-card {
  display: grid;
  gap: 14px;
}

.docs-section-head,
.doc-card-head,
.attachment-row,
.doc-footer-links {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}

.documents-grid,
.attachment-list,
.doc-summary-grid,
.doc-head-tags,
.attachment-actions,
.doc-footer-links {
  display: grid;
  gap: 10px;
}

.documents-grid {
  grid-template-columns: 1fr;
}

.doc-card {
  border: 1px solid var(--line);
  background: #fcfdff;
  border-radius: 18px;
  padding: 14px;
  display: grid;
  gap: 12px;
}

.doc-subtitle,
.attachment-meta {
  color: var(--muted);
  line-height: 1.5;
}

.doc-summary-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.attachment-list {
  gap: 8px;
}

.attachment-row {
  border: 1px solid var(--line);
  background: #ffffff;
  border-radius: 14px;
  padding: 12px;
}

.attachment-actions {
  justify-items: end;
}

.mini-pill-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.mini-pill {
  display: inline-flex;
  align-items: center;
  min-height: 28px;
  padding: 4px 8px;
  border-radius: 999px;
  border: 1px solid rgba(15, 140, 115, 0.14);
  background: rgba(15, 140, 115, 0.08);
  color: var(--accent-2);
  font-size: var(--text-xs);
}

.mini-pill-muted {
  border-color: var(--line);
  background: rgba(95, 111, 130, 0.08);
  color: var(--muted);
}

.empty-attachment {
  justify-content: flex-start;
}
'''
if style_add_after not in style:
    raise SystemExit('style insertion anchor not found')
style = style.replace(style_add_after, style_add)

style = style.replace(
    '@media (max-width: 1180px) {\n  .kpi-grid {\n    grid-template-columns: repeat(2, minmax(0, 1fr));\n  }\n\n  .hero-grid,\n  .profile-hero,\n  .section-grid,\n  .section-grid-compact-3 {\n    grid-template-columns: 1fr;\n  }\n}\n',
    '@media (max-width: 1180px) {\n  .kpi-grid {\n    grid-template-columns: repeat(2, minmax(0, 1fr));\n  }\n\n  .hero-grid,\n  .profile-hero,\n  .section-grid,\n  .section-grid-compact-3,\n  .doc-summary-grid {\n    grid-template-columns: 1fr;\n  }\n}\n'
)

style = style.replace(
    '  .header-row,\n  .header-actions,\n  .analysis-header-row,\n  .company-headline,\n  .preview-head,\n  .footer-note,\n  .result-picker {\n',
    '  .header-row,\n  .header-actions,\n  .analysis-header-row,\n  .company-headline,\n  .preview-head,\n  .footer-note,\n  .result-picker,\n  .docs-section-head,\n  .doc-card-head,\n  .attachment-row,\n  .doc-footer-links {\n'
)

style = style.replace(
    '  .top-search-grid,\n  .kpi-grid,\n  .kpi-grid-secondary,\n  .preview-grid {\n    grid-template-columns: 1fr;\n  }\n',
    '  .top-search-grid,\n  .kpi-grid,\n  .kpi-grid-secondary,\n  .preview-grid,\n  .doc-summary-grid {\n    grid-template-columns: 1fr;\n  }\n\n  .attachment-actions {\n    justify-items: start;\n  }\n'
)

server_path.write_text(server, encoding='utf-8')
app_path.write_text(app, encoding='utf-8')
style_path.write_text(style, encoding='utf-8')
print('patched')
