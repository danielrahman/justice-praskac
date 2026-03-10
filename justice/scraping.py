from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from justice.utils import (
    BASE_SITE,
    BASE_UI,
    absolute_ui_url,
    load_json_cache,
    logger,
    norm_key,
    norm_text,
    parse_czech_date,
    parse_href_params,
    save_json_cache,
    slug_hash,
    strip_accents,
)


_session_local = threading.local()


def _build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
        "Accept-Language": "cs-CZ,cs;q=0.9,en;q=0.8",
    })
    adapter = HTTPAdapter(
        max_retries=Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=0.6,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "HEAD"],
            raise_on_status=False,
        )
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def get_session() -> requests.Session:
    session = getattr(_session_local, "session", None)
    if session is None:
        session = _build_session()
        _session_local.session = session
    return session


def fetch_text(url: str) -> str:
    session = get_session()
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = session.get(url, timeout=60)
            response.raise_for_status()
            response.encoding = response.encoding or "utf-8"
            logger.info(f"fetch_text url={url} status={response.status_code}")
            return response.text
        except requests.RequestException as exc:
            last_error = exc
            logger.error(f"fetch_text error url={url} attempt={attempt + 1} error={exc}")
            if attempt < 2:
                time.sleep(1.2 * (attempt + 1))
    if last_error:
        raise last_error
    raise RuntimeError(f"Nepodařilo se načíst URL: {url}")


def response_is_pdf(response: requests.Response) -> bool:
    content_type = (response.headers.get("content-type") or "").lower()
    if "application/pdf" in content_type:
        return True
    return response.content[:4] == b"%PDF"


def _response_is_expired_download(response: requests.Response) -> bool:
    """Detect expired justice.cz download links (return HTML 'Nenalezeno')."""
    if response_is_pdf(response):
        return False
    if len(response.content) < 2000 and b"Nenalezeno" in response.content:
        return True
    return False


def resolve_live_download_url(url: str) -> str | None:
    session = get_session()
    try:
        response = session.get(url, timeout=45)
        response.raise_for_status()
        if response_is_pdf(response):
            return response.url or url
    except Exception:
        pass
    return None


def _download_pdf_response(url: str, session: requests.Session) -> requests.Response:
    """Download a PDF, raising ValueError immediately for expired links."""
    response = session.get(url, timeout=120)
    response.raise_for_status()
    if response_is_pdf(response):
        return response
    if _response_is_expired_download(response):
        raise ValueError(f"Download link expired: {url}")
    resolved = resolve_live_download_url(url)
    if resolved and resolved != url:
        response = session.get(resolved, timeout=120)
        response.raise_for_status()
    if not response_is_pdf(response):
        raise ValueError(f"URL did not return a PDF: {url}")
    return response


def fetch_binary(url: str, path: Path) -> Path:
    session = get_session()
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = _download_pdf_response(url, session)
            path.write_bytes(response.content)
            logger.info(f"fetch_binary url={url} size={len(response.content)}")
            return path
        except ValueError:
            raise
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1.0 * (attempt + 1))
    if last_error:
        raise last_error
    raise RuntimeError(f"Nepodařilo se stáhnout PDF: {url}")


def fetch_binary_bytes(url: str) -> bytes:
    session = get_session()
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = _download_pdf_response(url, session)
            logger.info(f"fetch_binary_bytes url={url} size={len(response.content)}")
            return response.content
        except ValueError:
            raise
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1.0 * (attempt + 1))
    if last_error:
        raise last_error
    raise RuntimeError(f"Nepodařilo se stáhnout PDF: {url}")


def clean_ico(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    return digits


def pick_legal_search_url(query: str) -> str:
    q = query.strip()
    ico = clean_ico(q)
    if len(ico) == 8:
        return f"{BASE_UI}rejstrik-$firma?ico={ico}&jenPlatne=PLATNE&polozek=50&typHledani=STARTS_WITH"
    return f"{BASE_UI}rejstrik-$firma?nazev={requests.utils.quote(q)}&jenPlatne=PLATNE&polozek=50&typHledani=STARTS_WITH"


def parse_search_results(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict[str, Any]] = []
    for container in soup.select("div.inner"):
        table = container.find("table", class_="result-details")
        links = container.find("ul", class_="result-links")
        if not table or not links:
            continue
        row_map: dict[str, str] = {}
        for tr in table.find_all("tr"):
            cells = tr.find_all(["th", "td"])
            texts = [norm_text(c.get_text(" ", strip=True)) for c in cells]
            if len(texts) >= 2 and texts[0]:
                row_map[texts[0].rstrip(":")] = texts[1]
            if len(texts) >= 4 and texts[2]:
                row_map[texts[2].rstrip(":")] = texts[3]
        action_urls: dict[str, str] = {}
        subject_id = None
        for link in links.find_all("a", href=True):
            label = norm_text(link.get_text(" ", strip=True))
            url = absolute_ui_url(link["href"])
            action_urls[label] = url
            params = parse_href_params(link["href"])
            if not subject_id and params.get("subjektId"):
                subject_id = params.get("subjektId")
        if not subject_id:
            continue
        ico = clean_ico(row_map.get("IČO") or row_map.get("ICO") or "")
        results.append(
            {
                "subject_id": subject_id,
                "name": row_map.get("Název subjektu", ""),
                "ico": ico,
                "ico_display": row_map.get("IČO") or row_map.get("ICO") or ico,
                "file_number": row_map.get("Spisová značka", ""),
                "registration_date": row_map.get("Den zápisu", ""),
                "address": row_map.get("Sídlo", ""),
                "current_extract_url": action_urls.get("Výpis platných"),
                "full_extract_url": action_urls.get("Úplný výpis"),
                "documents_url": action_urls.get("Sbírka listin"),
            }
        )
    return results


def search_companies(query: str) -> list[dict[str, Any]]:
    cache_name = f"search_{slug_hash(query)}"
    cached = load_json_cache(cache_name, 60 * 60 * 12)
    if cached is not None:
        return cached
    url = pick_legal_search_url(query)
    html = fetch_text(url)
    results = parse_search_results(html)
    save_json_cache(cache_name, results)
    return results


def is_section_label(label: str, value: str, extra: str = "") -> bool:
    """A section label is a row with a non-empty label and no value.

    The guard ``not label or value`` reads as ``(not label) or (value)`` —
    i.e. reject when the label is empty OR when a value is present.
    """
    label_n = norm_key(label)
    if not label or value:
        return False
    if any(key in label_n for key in [
        "statutarni organ",
        "predstavenstvo",
        "jednatel",
        "dozorci rada",
        "spravni rada",
        "spolecnik",
        "spolecnici",
        "akcionar",
        "jediny akcionar",
        "prokurista",
        "zakladni kapital",
        "akcie",
    ]):
        return True
    return False


def parse_extract_rows(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    title = norm_text(soup.find("h1").get_text(" ", strip=True)) if soup.find("h1") else ""
    subtitle = ""
    h2s = soup.find_all("h2")
    if h2s:
        subtitle = norm_text(h2s[0].get_text(" ", strip=True))
    rows = []
    for row in soup.select("div.div-row"):
        cells = row.select("div.div-cell")
        texts = [norm_text(cell.get_text(" ", strip=True)) for cell in cells]
        if texts:
            rows.append(texts)

    basic_info: dict[str, Any] = {}
    sections: list[dict[str, Any]] = []
    timeline_rows: list[dict[str, Any]] = []
    current_section: dict[str, Any] | None = None
    current_role: str | None = None

    for entry in rows:
        label = entry[0] if len(entry) > 0 else ""
        value = entry[1] if len(entry) > 1 else ""
        extra = entry[2] if len(entry) > 2 else ""
        timeline_rows.append({"label": label, "value": value, "history": extra})

        if is_section_label(label, value, extra):
            current_section = {"title": label.rstrip(":"), "items": []}
            sections.append(current_section)
            current_role = None
            continue

        if current_section:
            if label and not value and not extra:
                current_role = label.rstrip(":")
                continue
            if not label and value:
                current_section["items"].append(
                    {"role": current_role, "text": value, "history": extra}
                )
                continue
            if label and value and not extra and len(value) < 4:
                current_role = label.rstrip(":")
                continue
            if label and value:
                current_section["items"].append(
                    {"role": label.rstrip(":"), "text": value, "history": extra}
                )
                continue

        key = label.rstrip(":") if label else ""
        if not key:
            continue
        if key in basic_info:
            if isinstance(basic_info[key], list):
                basic_info[key].append(value)
            else:
                basic_info[key] = [basic_info[key], value]
        else:
            basic_info[key] = value

    pdf_link = soup.find("a", href=re.compile(r"print-pdf"))
    pdf_url = absolute_ui_url(pdf_link["href"]) if pdf_link else None
    return {
        "title": title,
        "subtitle": subtitle,
        "basic_info": basic_info,
        "sections": sections,
        "rows": timeline_rows,
        "pdf_url": pdf_url,
    }


def fetch_extract(subjekt_id: str, typ: str, force_refresh: bool = False) -> dict[str, Any]:
    cache_name = f"extract_{subjekt_id}_{typ.lower()}"
    if not force_refresh:
        cached = load_json_cache(cache_name, 60 * 60 * 24)
        if cached is not None:
            return cached
    url = f"{BASE_UI}rejstrik-firma.vysledky?subjektId={subjekt_id}&typ={typ}"
    parsed = parse_extract_rows(fetch_text(url))
    parsed["url"] = url
    save_json_cache(cache_name, parsed)
    return parsed
