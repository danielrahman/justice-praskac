from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from justice.scraping import fetch_binary, fetch_text
from justice.utils import (
    BASE_SITE,
    BASE_UI,
    CACHE_DIR,
    FINANCIAL_DOC_KEYWORDS,
    OCR_CACHE_VERSION,
    PDF_DIR,
    TEXT_DIR,
    absolute_ui_url,
    load_json_cache,
    logger,
    norm_key,
    norm_text,
    parse_czech_date,
    parse_href_params,
    save_json_cache,
    slug_hash,
)


def extract_attachment_page_hint(label: str | None) -> int | None:
    if not label:
        return None
    match = re.search(r"počet stran:\s*(\d+)", label, flags=re.I)
    return int(match.group(1)) if match else None


def extract_attachment_size_kb(label: str | None) -> float | None:
    if not label:
        return None
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*kB", label, flags=re.I)
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
    for link in soup.find_all("a", href=re.compile(r"/ias/content/download\?id=")):
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


def parse_document_list(subjekt_id: str, force_refresh: bool = False) -> list[dict[str, Any]]:
    cache_name = f"docs_{subjekt_id}"
    if not force_refresh:
        cached = load_json_cache(cache_name, 60 * 60 * 24)
        if cached is not None:
            return cached
    url = f"{BASE_UI}vypis-sl-firma?subjektId={subjekt_id}"
    html = fetch_text(url)
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    docs: list[dict[str, Any]] = []
    if len(tables) < 2:
        return docs
    for tr in tables[1].find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 6:
            continue
        link = tds[0].find("a", href=True)
        if not link:
            continue
        detail_url = absolute_ui_url(link["href"])
        params = parse_href_params(link["href"])
        doc_type = norm_text(tds[1].get_text(" ", strip=True))
        years = [int(y) for y in re.findall(r"\[(20\d{2})\]", doc_type)]
        item = {
            "document_number": norm_text(link.get_text(" ", strip=True)),
            "type": doc_type,
            "created_date": parse_czech_date(norm_text(tds[2].get_text(" ", strip=True))),
            "received_date": parse_czech_date(norm_text(tds[3].get_text(" ", strip=True))),
            "filed_date": parse_czech_date(norm_text(tds[4].get_text(" ", strip=True))),
            "pages": int(re.sub(r"\D", "", tds[5].get_text(" ", strip=True)) or 0),
            "detail_url": detail_url,
            "document_id": params.get("dokument"),
            "spis": params.get("spis"),
            "subjekt_id": params.get("subjektId") or subjekt_id,
            "years": years,
        }
        docs.append(item)
    save_json_cache(cache_name, docs)
    return docs


def is_financial_document(doc: dict[str, Any]) -> bool:
    text = norm_key(doc.get("type", ""))
    return any(key in text for key in [norm_key(k) for k in FINANCIAL_DOC_KEYWORDS])


def financial_doc_score(doc: dict[str, Any]) -> int:
    type_key = norm_key(doc.get("type") or "")
    pages = int(doc.get("pages") or 0)
    score = pages
    if "vyrocni zprava" in type_key:
        score += 80
    if "zprava auditora" in type_key:
        score += 35
    if "ucetni zaverka" in type_key:
        score += 30
    if "vykaz zisku" in type_key or "vzaz" in type_key:
        score += 20
    if "rozvaha" in type_key:
        score += 20
    if "priloha" in type_key and "vyrocni zprava" not in type_key:
        score -= 12
    if pages == 0:
        score -= 120
    if len(doc.get("years") or []) >= 2:
        score += 12
    return score


def pick_recent_financial_docs(docs: list[dict[str, Any]], max_years: int = 5, force_refresh_details: bool = False) -> list[dict[str, Any]]:
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


def pdf_page_count(pdf_path: Path) -> int:
    try:
        output = subprocess.check_output(["pdfinfo", str(pdf_path)], text=True, errors="ignore")
        match = re.search(r"Pages:\s+(\d+)", output)
        count = int(match.group(1)) if match else 0
        logger.info(f"pdf_page_count path={pdf_path} pages={count}")
        return count
    except Exception:
        return 0


def extract_text_digital(pdf_path: Path, txt_path: Path) -> str:
    logger.info(f"extract_text_digital path={pdf_path}")
    if txt_path.exists() and txt_path.stat().st_size > 0:
        return txt_path.read_text(encoding="utf-8", errors="ignore")
    subprocess.run(["pdftotext", "-layout", str(pdf_path), str(txt_path)], check=False)
    if txt_path.exists():
        return txt_path.read_text(encoding="utf-8", errors="ignore")
    return ""


def ocr_selected_pages(pdf_path: Path, txt_path: Path) -> str:
    if txt_path.exists() and txt_path.stat().st_size > 0:
        return txt_path.read_text(encoding="utf-8", errors="ignore")
    page_count_val = pdf_page_count(pdf_path)
    if page_count_val <= 0:
        return ""
    logger.info(f"ocr_selected_pages path={pdf_path} page_count={page_count_val}")
    if page_count_val <= 40:
        selected_pages = list(range(1, page_count_val + 1))
    elif page_count_val <= 80:
        selected_pages = list(range(1, 7)) + list(range(max(7, page_count_val - 14), page_count_val + 1))
    elif page_count_val <= 200:
        selected_pages = [1, 2, 3, 4] + list(range(max(5, page_count_val - 12), page_count_val + 1))
    else:
        selected_pages = [1, 2, 3] + list(range(max(4, page_count_val - 9), page_count_val + 1))
    selected_pages = sorted(set(p for p in selected_pages if 1 <= p <= page_count_val))
    all_text: list[str] = []
    with tempfile.TemporaryDirectory() as tmpdir:
        for page in selected_pages:
            prefix = Path(tmpdir) / f"page{page}"
            subprocess.run(
                ["pdftoppm", "-f", str(page), "-l", str(page), "-r", "220", "-png", str(pdf_path), str(prefix)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            image = Path(f"{prefix}-{page}.png")
            if not image.exists():
                alt = Path(f"{prefix}-1.png")
                image = alt if alt.exists() else image
            if not image.exists():
                continue
            page_text = ""
            for psm in ("4", "6"):
                try:
                    proc = subprocess.run(
                        ["tesseract", str(image), "stdout", "-l", "ces+eng", "--psm", psm],
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=45,
                    )
                except subprocess.TimeoutExpired:
                    continue
                candidate = proc.stdout.strip()
                if not candidate:
                    continue
                if len(candidate) > len(page_text):
                    page_text = candidate
                candidate_key = norm_key(candidate)
                if any(token in candidate_key for token in ["rozvaha", "vykaz zisku a ztraty", "aktiva celkem", "trzby z prodeje"]):
                    page_text = candidate
                    break
            if page_text:
                all_text.append(f"\n\n--- PAGE {page} ---\n{page_text}")
    combined = "\n".join(all_text)
    if combined:
        txt_path.write_text(combined, encoding="utf-8")
    return combined


def get_pdf_text(pdf_url: str) -> dict[str, Any]:
    pdf_id = slug_hash(pdf_url)
    pdf_path = PDF_DIR / f"{pdf_id}.pdf"
    text_path = TEXT_DIR / f"{pdf_id}.txt"
    ocr_path = TEXT_DIR / f"{pdf_id}.{OCR_CACHE_VERSION}.ocr.txt"
    fetch_binary(pdf_url, pdf_path)
    digital = extract_text_digital(pdf_path, text_path)
    useful_len = len(re.sub(r"\s+", "", digital))
    page_count_val = pdf_page_count(pdf_path)
    ocr = ocr_selected_pages(pdf_path, ocr_path)
    digital_key = norm_key(digital)
    ocr_key = norm_key(ocr)
    if useful_len >= 2500 and not any(token in ocr_key for token in ["aktiva celkem", "vykaz zisku a ztraty", "trzby z prodeje"]):
        return {
            "text": digital,
            "digital_text": digital,
            "ocr_text": ocr or None,
            "mode": "digital",
            "page_count": page_count_val,
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
        "page_count": page_count_val,
        "pdf_path": str(pdf_path),
    }


def build_metric_source_text(pdf_text: dict[str, Any]) -> str:
    pieces: list[str] = []
    seen: set[str] = set()
    for key in ("digital_text", "ocr_text", "text"):
        value = str(pdf_text.get(key) or "").strip()
        compact = re.sub(r"\s+", "", value)
        if not value or len(compact) < 40:
            continue
        if value in seen:
            continue
        seen.add(value)
        pieces.append(value)
    return "\n\n".join(pieces)


def detect_unit_multiplier(text: str) -> int:
    sample = norm_key(text[:6000])
    if "v tisicich kc" in sample or "v tisicich k" in sample or "v tis. kc" in sample:
        return 1000
    if "v mil. kc" in sample or "v milionech kc" in sample:
        return 1_000_000
    return 1000
