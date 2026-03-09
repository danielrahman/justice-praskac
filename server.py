#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import subprocess
import tempfile
import time
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from anthropic import Anthropic
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_UI = "https://or.justice.cz/ias/ui/"
BASE_SITE = "https://or.justice.cz"
ROOT_DIR = Path(__file__).resolve().parent
CACHE_DIR = Path(os.getenv("JUSTICE_CACHE_DIR", str(ROOT_DIR / "cache")))
CACHE_DIR.mkdir(parents=True, exist_ok=True)
PDF_DIR = CACHE_DIR / "pdfs"
PDF_DIR.mkdir(exist_ok=True)
TEXT_DIR = CACHE_DIR / "text"
TEXT_DIR.mkdir(exist_ok=True)
JSON_DIR = CACHE_DIR / "json"
JSON_DIR.mkdir(exist_ok=True)
DB_PATH = Path(os.getenv("JUSTICE_DB_PATH", str(ROOT_DIR / "app_state.db")))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
    "Accept-Language": "cs-CZ,cs;q=0.9,en;q=0.8",
})
SESSION.mount(
    "https://",
    HTTPAdapter(
        max_retries=Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=0.6,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "HEAD"],
            raise_on_status=False,
        )
    ),
)
SESSION.mount(
    "http://",
    HTTPAdapter(
        max_retries=Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=0.6,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "HEAD"],
            raise_on_status=False,
        )
    ),
)

MONTHS = {
    "ledna": 1,
    "února": 2,
    "brezna": 3,
    "března": 3,
    "dubna": 4,
    "května": 5,
    "cervna": 6,
    "června": 6,
    "cervence": 7,
    "července": 7,
    "srpna": 8,
    "zari": 9,
    "září": 9,
    "rijna": 10,
    "října": 10,
    "listopadu": 11,
    "prosince": 12,
}

FINANCIAL_DOC_KEYWORDS = [
    "účetní závěrka",
    "ucetni zaverka",
    "výroční zpráva",
    "vyrocni zprava",
    "zpráva auditora",
    "zprava auditora",
    "zpráva o vztazích",
    "zprava o vztazich",
]

METRIC_PATTERNS = {
    "revenue": [
        "trzby z prodeje vyrobku a sluzeb",
        "trdby z prodeje vyrobku a sluzeb",
        "traby z prodeje vyrobku a sluzeb",
        "treby z prodeje vyrobku a sluzeb",
        "treby z prodeje vyrobku a sluzeb",
        "trzby z prodeje sluzeb",
        "tidby z prodeje sluzeb",
        "triby z hlavnich cinnosti",
        "cisty obrat za ucetni obdobi",
        "vynosy celkem",
    ],
    "operating_profit": [
        "provozni vysledek hospodareni",
        "provozni vysledek hospodafeni",
        "vysledek hospodareni z provozni cinnosti",
    ],
    "net_profit": [
        "vysledek hospodareni za ucetni obdobi",
        "vysledek hospodafeni za ucetni obdobi",
        "vysledek hospodafeni za ucetni obdobs",
        "vysledek hospodareni po zdaneni",
        "vysledek hospodafeni po zdaneni",
        "vysledek hospodareni bezneho ucetniho obdobi",
        "vysledek hospodafeni bezneho ucetniho obdobi",
        "vysledek hospodafeni za bezny rok",
        "zisk nebo ztrata za ucetni obdobi",
    ],
    "assets": [
        "aktiva celkem",
        "suma aktiv",
    ],
    "equity": [
        "vlastni kapital",
    ],
    "liabilities": [
        "cizi zdroje",
        "zavazky celkem",
    ],
    "debt": [
        "bankovni uvery a vypomoci",
        "zavazky k uverovym institucim",
        "zavazky k iverovym institucim",
        "zavazky k tiverovym institucim",
        "zavazky k uverovym institucim:",
        "zavazky k ave rowym institucim",
    ],
}

SECTION_PRIORITY = [
    "Statutární orgán",
    "Jednatel",
    "Představenstvo",
    "Dozorčí rada",
    "Správní rada",
    "Společníci",
    "Akcionář",
    "Jediný akcionář",
    "Prokurista",
]

PROFILE_CACHE_VERSION = "v7_all_attachments_redesign"
OCR_CACHE_VERSION = "v3_all_attachments_refresh"
PROFILE_CACHE_TTL_SECONDS = int(os.getenv("JUSTICE_PROFILE_CACHE_TTL_SECONDS", str(60 * 60 * 24 * 3)))
AI_MODEL = os.getenv("JUSTICE_AI_MODEL", "claude_sonnet_4_5")
AI_ENABLED = os.getenv("JUSTICE_ENABLE_AI", "1") != "0"
AI_TIMEOUT_SECONDS = int(os.getenv("JUSTICE_AI_TIMEOUT_SECONDS", "90"))


def now_ts() -> float:
    return time.time()


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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
    return conn


def save_history_entry(visitor_id: str | None, profile: dict[str, Any], query: str | None = None) -> None:
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
                str(profile.get("subject_id") or ""),
                str(profile.get("ico") or ""),
                str(profile.get("name") or ""),
                query,
                json.dumps(profile, ensure_ascii=False),
                visitor_id,
            ),
        )
        conn.commit()
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


def strip_accents(value: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch)
    )


def norm_text(value: str) -> str:
    value = value.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def norm_key(value: str) -> str:
    value = strip_accents(norm_text(value)).lower()
    value = value.replace("–", "-").replace("—", "-")
    return value


def slug_hash(value: str) -> str:
    return hashlib.md5(value.encode("utf-8")).hexdigest()


def load_json_cache(name: str, max_age_seconds: int) -> Any | None:
    path = JSON_DIR / f"{name}.json"
    if not path.exists():
        return None
    if now_ts() - path.stat().st_mtime > max_age_seconds:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_json_cache(name: str, data: Any) -> None:
    path = JSON_DIR / f"{name}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_text(url: str) -> str:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = SESSION.get(url, timeout=60)
            response.raise_for_status()
            response.encoding = response.encoding or "utf-8"
            return response.text
        except requests.RequestException as exc:
            last_error = exc
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


def resolve_live_download_url(url: str) -> str | None:
    for attempt in range(3):
        try:
            response = SESSION.get(url, timeout=45)
            response.raise_for_status()
            if response_is_pdf(response):
                return response.url or url
        except Exception:
            if attempt < 2:
                time.sleep(0.8 * (attempt + 1))
    return None


def fetch_binary(url: str, path: Path) -> Path:
    if path.exists() and path.stat().st_size > 0:
        return path
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = SESSION.get(url, timeout=120)
            response.raise_for_status()
            if not response_is_pdf(response):
                resolved = resolve_live_download_url(url)
                if resolved and resolved != url:
                    response = SESSION.get(resolved, timeout=120)
                    response.raise_for_status()
            if not response_is_pdf(response):
                raise ValueError(f"URL did not return a PDF: {url}")
            path.write_bytes(response.content)
            return path
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1.0 * (attempt + 1))
    if last_error:
        raise last_error
    raise RuntimeError(f"Nepodařilo se stáhnout PDF: {url}")


def public_error_message(exc: Exception) -> str:
    text = norm_text(str(exc) or "")
    lower = text.lower()
    if isinstance(exc, requests.RequestException):
        return "Justice.cz teď neodpovídá stabilně. Zkus to prosím znovu za chvíli."
    if "remote end closed connection" in lower or "remotedisconnected" in lower:
        return "Justice.cz během načítání přerušila spojení. Zkus to prosím znovu."
    if "read timed out" in lower or "timed out" in lower:
        return "Načítání z justice.cz trvalo příliš dlouho. Zkus to prosím znovu."
    if text:
        return text
    return "Načtení veřejných podkladů se nepodařilo dokončit. Zkus to prosím znovu."


def absolute_ui_url(href: str) -> str:
    return urljoin(BASE_UI, href)


def parse_href_params(href: str) -> dict[str, str]:
    qs = parse_qs(urlparse(href).query)
    return {k: v[0] for k, v in qs.items() if v}


def parse_czech_date(value: str | None) -> str | None:
    if not value:
        return None
    text = norm_text(value)
    if not text:
        return None
    if re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d{4}", text):
        day, month, year = text.split(".")[:3]
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    match = re.match(r"(\d{1,2})\.\s*([A-Za-zÁ-ž]+)\s+(\d{4})", text)
    if match:
        day = int(match.group(1))
        month_name = norm_key(match.group(2))
        month = MONTHS.get(month_name)
        year = int(match.group(3))
        if month:
            return f"{year:04d}-{month:02d}-{day:02d}"
    return None


def iso_to_display(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).strftime("%d.%m.%Y")
    except Exception:
        return value


def days_between(date_a: str | None, date_b: str | None) -> int | None:
    try:
        if not date_a or not date_b:
            return None
        a = datetime.fromisoformat(date_a)
        b = datetime.fromisoformat(date_b)
        return (b - a).days
    except Exception:
        return None


def parse_number_candidates(raw: str) -> list[int]:
    text = raw.replace("\xa0", " ")
    matches = re.findall(r"-?\d{1,3}(?:[ .]\d{3})+(?:,\d+)?|-?\d+(?:,\d+)?", text)
    values: list[int] = []
    for match in matches:
        cleaned = match.replace(" ", "").replace(".", "").replace(",", ".")
        try:
            number = float(cleaned)
            if abs(number) >= 1:
                values.append(int(round(number)))
        except Exception:
            continue
    return values


def parse_metric_line(raw_line: str) -> tuple[int, int] | None:
    matches = re.findall(r"-?\d{1,3}(?:[ .]\d{3})+|-?\d+", raw_line)
    cleaned = []
    for match in matches:
        num = int(match.replace(" ", "").replace(".", ""))
        cleaned.append(num)
    joined = [n for n in cleaned if abs(n) >= 1000]
    if len(joined) >= 2:
        return joined[-2], joined[-1]
    significant = [n for n in cleaned if abs(n) >= 100]
    if len(significant) >= 2:
        return significant[-2], significant[-1]
    return None


def parse_adjacent_metric(index: int, lines: list[str], window: int = 6) -> tuple[int, int] | None:
    for offset in range(1, window + 1):
        if index + offset >= len(lines):
            break
        candidate = norm_text(lines[index + offset])
        if not candidate:
            continue
        pair = parse_metric_line(candidate)
        if pair:
            return pair
    return None


def parse_line_two_values(raw_line: str) -> tuple[int, int] | None:
    parts = [p.strip() for p in re.split(r"\s{2,}", raw_line.strip()) if p.strip()]
    numeric_parts: list[int] = []
    for part in parts:
        if re.search(r"\d", part):
            nums = parse_number_candidates(part)
            if nums:
                numeric_parts.append(nums[-1])
    if len(numeric_parts) >= 2:
        return numeric_parts[-2], numeric_parts[-1]
    nums = parse_number_candidates(raw_line)
    significant = [n for n in nums if abs(n) >= 100]
    if len(significant) >= 2:
        return significant[-2], significant[-1]
    if len(nums) >= 2:
        return nums[-2], nums[-1]
    return None


def is_probable_year(value: int) -> bool:
    return 1900 <= abs(value) <= 2105


def looks_like_year_header(line_key: str) -> bool:
    years = re.findall(r"\b20\d{2}\b", line_key)
    compact = re.sub(r"[^0-9 ]", " ", line_key)
    tokens = [t for t in compact.split() if t]
    if len(years) >= 2 and len(tokens) <= 4:
        return True
    return False


def split_digit_groups(raw_line: str) -> list[str]:
    cleaned = raw_line
    for ch in "|[](){}:,;~=“”‘’'\"":
        cleaned = cleaned.replace(ch, " ")
    cleaned = cleaned.replace("§", "5")
    cleaned = cleaned.replace("—", "-").replace("–", "-")
    return re.findall(r"-?\d+", cleaned)


def trim_leading_label_groups(groups: list[str]) -> list[str]:
    trimmed = list(groups)
    if not trimmed:
        return trimmed
    if len(trimmed) >= 4 and len(trimmed[0].lstrip('+-')) <= 2:
        return trimmed[1:]
    if len(trimmed) == 3 and len(trimmed[0].lstrip('+-')) <= 2 and all(len(g.lstrip('+-')) >= 4 for g in trimmed[1:]):
        return trimmed[1:]
    return trimmed


def combine_digit_groups(groups: list[str]) -> int | None:
    if not groups:
        return None
    sign = -1 if groups[0].startswith("-") else 1
    normalized = [g.lstrip("+-") for g in groups]
    if len(normalized) == 1:
        try:
            return sign * int(normalized[0])
        except Exception:
            return None
    head = normalized[0]
    tail = normalized[1:]
    if not head or any(len(part) != 3 for part in tail):
        return None
    try:
        return sign * int(head + "".join(tail))
    except Exception:
        return None


def extract_metric_pair(raw_line: str, min_value: int = 100) -> tuple[int, int] | None:
    groups = [g for g in split_digit_groups(raw_line) if not is_probable_year(int(g.lstrip('+-'))) ]
    groups = trim_leading_label_groups(groups)
    if len(groups) >= 4 and all(len(g.lstrip('+-')) == 3 for g in groups[-4:]):
        current = combine_digit_groups(groups[-4:-2])
        previous = combine_digit_groups(groups[-2:])
        if current is not None and previous is not None:
            return current, previous
    if len(groups) >= 3 and all(len(g.lstrip('+-')) == 3 for g in groups[-3:]):
        current = combine_digit_groups(groups[-3:-1])
        previous = combine_digit_groups(groups[-2:])
        if current is not None and previous is not None and abs(previous) >= min_value:
            return current, previous
    candidates: list[tuple[int, int, int]] = []
    for second_len in range(1, min(3, len(groups)) + 1):
        for first_len in range(1, min(3, len(groups) - second_len) + 1):
            first_groups = groups[-(first_len + second_len):-second_len]
            second_groups = groups[-second_len:]
            first = combine_digit_groups(first_groups)
            second = combine_digit_groups(second_groups)
            if first is None or second is None:
                continue
            if abs(first) < min_value or abs(second) < min_value:
                continue
            ratio = max(abs(first), abs(second)) / max(min(abs(first), abs(second)), 1)
            score = first_len + second_len
            if ratio <= 50:
                score += 3
            if ratio <= 10:
                score += 3
            if len(groups[: -(first_len + second_len)]) <= 2:
                score += 1
            candidates.append((score, first, second))
    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1], candidates[0][2]
    nums = parse_number_candidates(raw_line)
    significant = [n for n in nums if abs(n) >= min_value and not is_probable_year(n)]
    if len(significant) >= 2:
        return significant[-2], significant[-1]
    return None


def find_nearby_metric_pair(index: int, lines: list[str], window: int = 4) -> tuple[int, int] | None:
    for offset in range(0, window + 1):
        if index + offset >= len(lines):
            break
        candidate = norm_text(lines[index + offset])
        if not candidate:
            continue
        candidate_key = norm_key(candidate)
        if looks_like_year_header(candidate_key):
            continue
        pair = extract_metric_pair(candidate) or parse_line_two_values(candidate)
        if pair:
            return pair
    return None


def line_matches_metric(metric: str, line_key: str) -> bool:
    if metric == "revenue":
        revenue_tokens = ["trzby", "trdby", "traby", "treby", "triby", "tidby", "itreby", "itreby"]
        return any(token in line_key for token in revenue_tokens) and any(
            token in line_key for token in ["prodeje", "sluzeb", "hlavnich cinnosti", "cisty obrat", "vynosy celkem"]
        )
    if metric == "operating_profit":
        return "provozni vysledek hospod" in line_key or "vysledek hospodareni z provozni cinnosti" in line_key
    if metric == "net_profit":
        return (
            ("vysledek hospod" in line_key or "zisk nebo ztrata" in line_key)
            and (
                "po zdan" in line_key
                or ("za" in line_key and "obdobi" in line_key)
                or "bezneho" in line_key
                or "bezny rok" in line_key
            )
        )
    if metric == "assets":
        return "aktiva celkem" in line_key or "suma aktiv" in line_key or "pasiva celkem" in line_key
    if metric == "equity":
        return "vlastni kapital" in line_key
    if metric == "liabilities":
        return "cizi zdroje" in line_key or "zavazky celkem" in line_key
    if metric == "debt":
        return (
            "bankovni uvery a vypomoci" in line_key
            or (
                "zavazky k" in line_key
                and "instituc" in line_key
                and any(token in line_key for token in ["uver", "iver", "uve", "avero", "ivero", "uverovym"])
            )
        )
    return False


def find_metric_pair_for_window(metric: str, index: int, lines: list[str], window: int = 4) -> tuple[int, int] | None:
    for offset in range(0, window + 1):
        if index + offset >= len(lines):
            break
        candidate = norm_text(lines[index + offset])
        candidate_key = norm_key(candidate)
        if not candidate or looks_like_year_header(candidate_key):
            continue
        if not line_matches_metric(metric, candidate_key) and not any(pattern in candidate_key for pattern in METRIC_PATTERNS.get(metric, [])):
            continue
        pair = extract_metric_pair(candidate) or parse_line_two_values(candidate)
        if pair:
            return pair
    return None


def parse_loose_number(raw: str) -> int | None:
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return None
    try:
        return int(digits)
    except Exception:
        return None


def extract_debt_pair(lines: list[str]) -> tuple[int, int] | None:
    pairs: list[tuple[int, int]] = []
    for idx, raw_line in enumerate(lines):
        line = norm_text(raw_line)
        line_key = norm_key(line)
        if not line:
            continue
        if not (line_matches_metric("debt", line_key) or any(pattern in line_key for pattern in METRIC_PATTERNS["debt"])):
            continue
        pair = extract_metric_pair(line)
        if pair:
            pairs.append(pair)
            continue
        if idx + 2 < len(lines):
            first = parse_loose_number(lines[idx + 2])
            if first is not None and abs(first) >= 100:
                for follow in range(idx + 3, min(len(lines), idx + 7)):
                    candidate = norm_text(lines[follow])
                    candidate_key = norm_key(candidate)
                    if not candidate:
                        continue
                    if line_matches_metric("debt", candidate_key):
                        pair2 = extract_metric_pair(candidate)
                        if pair2:
                            pairs.append((first, pair2[0]))
                            break
    if not pairs:
        return None
    current = sum(pair[0] for pair in pairs)
    previous = sum(pair[1] for pair in pairs)
    return current, previous


def extract_net_profit_pair(lines: list[str]) -> tuple[int, int] | None:
    preferred: tuple[int, int] | None = None
    fallback: tuple[int, int] | None = None
    for raw_line in lines:
        line = norm_text(raw_line)
        line_key = norm_key(line)
        if "vysledek hospod" not in line_key:
            continue
        if not ("po zdan" in line_key or ("za" in line_key and "obdobi" in line_key) or "bezny rok" in line_key):
            continue
        pair = extract_metric_pair(line)
        if pair:
            if "za" in line_key and "obdobi" in line_key:
                return pair
            if "po zdan" in line_key:
                preferred = pair
            elif fallback is None:
                fallback = pair
    return preferred or fallback


def extract_tail_monetary_value(raw_line: str) -> int | None:
    groups = [g for g in split_digit_groups(raw_line) if not is_probable_year(int(g.lstrip('+-')))]
    groups = trim_leading_label_groups(groups)
    if len(groups) >= 2 and all(len(g.lstrip('+-')) == 3 for g in groups[-2:]):
        return combine_digit_groups(groups[-2:])
    if groups:
        return combine_digit_groups([groups[-1]])
    return None


def extract_equity_from_statement_of_changes(lines: list[str], doc_year: int | None) -> tuple[int, int] | None:
    if not doc_year:
        return None
    current_val: int | None = None
    previous_val: int | None = None
    prev_year = doc_year - 1
    for raw_line in lines:
        line = norm_text(raw_line)
        line_key = norm_key(line)
        if not line:
            continue
        if f"31.12.{doc_year}" in line_key and any(token in line_key for token in ["zustatek", "zostatek"]):
            current_val = extract_tail_monetary_value(line)
        if any(marker in line_key for marker in [f"31.12.{prev_year}", f"1.1.{doc_year}"]) and any(token in line_key for token in ["zustatek", "zostatek"]):
            previous_val = extract_tail_monetary_value(line)
    if current_val is not None and previous_val is not None:
        return current_val, previous_val
    return None


def extract_net_profit_from_equity_changes(lines: list[str]) -> tuple[int | None, int | None] | None:
    for raw_line in lines:
        line = norm_text(raw_line)
        line_key = norm_key(line)
        if "vysledek hospod" not in line_key:
            continue
        if not ("rok" in line_key or "obdobi" in line_key):
            continue
        pair = extract_metric_pair(line)
        if pair:
            return pair
        tail = extract_tail_monetary_value(line)
        if tail is not None:
            return tail, None
    return None


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
        return int(match.group(1)) if match else 0
    except Exception:
        return 0


def extract_text_digital(pdf_path: Path, txt_path: Path) -> str:
    if txt_path.exists() and txt_path.stat().st_size > 0:
        return txt_path.read_text(encoding="utf-8", errors="ignore")
    subprocess.run(["pdftotext", "-layout", str(pdf_path), str(txt_path)], check=False)
    if txt_path.exists():
        return txt_path.read_text(encoding="utf-8", errors="ignore")
    return ""


def ocr_selected_pages(pdf_path: Path, txt_path: Path) -> str:
    if txt_path.exists() and txt_path.stat().st_size > 0:
        return txt_path.read_text(encoding="utf-8", errors="ignore")
    page_count = pdf_page_count(pdf_path)
    if page_count <= 0:
        return ""
    if page_count <= 40:
        selected_pages = list(range(1, page_count + 1))
    elif page_count <= 80:
        selected_pages = list(range(1, 7)) + list(range(max(7, page_count - 14), page_count + 1))
    elif page_count <= 200:
        selected_pages = [1, 2, 3, 4] + list(range(max(5, page_count - 12), page_count + 1))
    else:
        selected_pages = [1, 2, 3] + list(range(max(4, page_count - 9), page_count + 1))
    selected_pages = sorted(set(p for p in selected_pages if 1 <= p <= page_count))
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


def normalize_timeline_outliers(timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    metrics = ["revenue", "operating_profit", "net_profit", "assets", "equity", "liabilities", "debt"]
    ordered = sorted(timeline, key=lambda x: x["year"])
    for metric in metrics:
        for idx, row in enumerate(ordered):
            value = row.get(metric)
            if value is None or value == 0:
                continue
            neighbors: list[float] = []
            for offset in (-2, -1, 1, 2):
                pos = idx + offset
                if 0 <= pos < len(ordered):
                    neighbor_value = ordered[pos].get(metric)
                    if neighbor_value not in (None, 0):
                        neighbors.append(abs(neighbor_value))
            if not neighbors:
                continue
            baseline = sorted(neighbors)[len(neighbors) // 2]
            if baseline <= 0:
                continue
            ratio = abs(value) / baseline
            if ratio >= 200 and abs(value / 1000) / baseline <= 5:
                row[metric] = round(value / 1000, 2)
            elif ratio >= 200 and abs(value) > 100000:
                row[metric] = None
    return ordered


def sanitize_financial_rows(timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for entry in timeline:
        revenue = entry.get("revenue")
        net_profit = entry.get("net_profit")
        assets = entry.get("assets")
        equity = entry.get("equity")
        liabilities = entry.get("liabilities")
        debt = entry.get("debt")

        if revenue is not None and assets is not None and revenue < 5 and assets > 100:
            entry["revenue"] = None
            revenue = None
        if revenue is not None and net_profit is not None and revenue > 0 and abs(net_profit / revenue) > 1.5:
            entry["net_profit"] = None
            net_profit = None
        if assets is not None and equity is not None and (equity < -assets or equity > assets * 1.2):
            entry["equity"] = None
            equity = None
        if assets is not None and liabilities is not None and liabilities > assets * 1.2:
            entry["liabilities"] = None
            liabilities = None
        if assets is not None and debt is not None and debt > assets * 1.2 and debt > 50:
            entry["debt"] = None
    return timeline


def recalculate_timeline_ratios(timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for entry in timeline:
        revenue = entry.get("revenue")
        net_profit = entry.get("net_profit")
        assets = entry.get("assets")
        equity = entry.get("equity")
        liabilities = entry.get("liabilities")
        debt = entry.get("debt")
        entry["net_margin_pct"] = round((net_profit / revenue) * 100, 1) if revenue and net_profit is not None else None
        entry["equity_ratio_pct"] = round((equity / assets) * 100, 1) if assets and equity is not None else None
        entry["liability_ratio_pct"] = round((liabilities / assets) * 100, 1) if assets and liabilities is not None else None
        entry["debt_to_revenue_pct"] = round((debt / revenue) * 100, 1) if revenue and debt is not None else None
    return timeline


def best_role_for_section(title: str, role: str | None) -> str | None:
    role_clean = norm_text(role or "")
    if role_clean:
        return role_clean
    title_clean = norm_text(title or "")
    return title_clean or None


def extract_birth_date(text: str) -> str | None:
    match = re.search(r"dat\.\s*nar\.\s*((?:\d{1,2}\.\s*[A-Za-zÁ-ž]+\s+\d{4})|(?:\d{1,2}\.\d{1,2}\.\d{4}))", text, flags=re.I)
    return norm_text(match.group(1)) if match else None


def extract_owner_name(text: str) -> str | None:
    clean = norm_text(text)
    if not clean:
        return None
    name = re.split(r"\s+,\s*IČ[: ]|\s+,\s*ICO[: ]|\s+IČ[: ]|\s+ICO[: ]", clean, maxsplit=1, flags=re.I)[0]
    return name.strip(" ,") or None


def owner_item_is_primary(role: str | None, text: str | None) -> bool:
    role_key = norm_key(role or "")
    text_key = norm_key(text or "")
    return any(key in role_key for key in ["spolecnik", "akcionar", "jediny akcionar"]) or (
        not role_key and any(key in text_key for key in [" a.s.", " s.r.o.", " družstvo", " fund", " nadace"]) 
    )


def dedupe_people(items: list[dict[str, Any]], key_name: str = "name") -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        key = norm_key(str(item.get(key_name) or item.get("raw") or ""))
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def get_pdf_text(pdf_url: str) -> dict[str, Any]:
    pdf_id = slug_hash(pdf_url)
    pdf_path = PDF_DIR / f"{pdf_id}.pdf"
    text_path = TEXT_DIR / f"{pdf_id}.txt"
    ocr_path = TEXT_DIR / f"{pdf_id}.{OCR_CACHE_VERSION}.ocr.txt"
    fetch_binary(pdf_url, pdf_path)
    digital = extract_text_digital(pdf_path, text_path)
    useful_len = len(re.sub(r"\s+", "", digital))
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


def monetary_to_million_czk(value: int | None, multiplier: int) -> float | None:
    if value is None:
        return None
    return round((value * multiplier) / 1_000_000, 2)


def extract_financial_metrics_from_text(text: str, doc_year: int | None) -> dict[str, Any]:
    lines = text.splitlines()
    multiplier = detect_unit_multiplier(text)
    found: dict[str, tuple[int, int]] = {}
    found_scores: dict[str, int] = {}

    for index, raw_line in enumerate(lines):
        line = norm_text(raw_line)
        line_key = norm_key(line)
        if not line or len(line) < 4 or looks_like_year_header(line_key):
            continue
        context_lines = [raw_line]
        if index + 1 < len(lines):
            context_lines.append(lines[index + 1])
        if index + 2 < len(lines):
            context_lines.append(lines[index + 2])
        context = " \n".join(context_lines)
        context_key = norm_key(context)
        digit_groups = [g for g in split_digit_groups(raw_line) if not is_probable_year(int(g.lstrip('+-')))]
        for metric, patterns in METRIC_PATTERNS.items():
            line_pattern_hit = any(pattern in line_key for pattern in patterns)
            context_pattern_hit = any(pattern in context_key for pattern in patterns)
            line_fuzzy_hit = line_matches_metric(metric, line_key)
            context_fuzzy_hit = line_matches_metric(metric, context_key)
            if not (line_pattern_hit or context_pattern_hit or line_fuzzy_hit or context_fuzzy_hit):
                continue
            pair = None
            candidate_score = 0
            if line_pattern_hit or line_fuzzy_hit:
                pair = extract_metric_pair(raw_line)
                if pair:
                    candidate_score += 3
                if not pair:
                    pair = parse_line_two_values(raw_line)
                    if pair:
                        candidate_score += 2
                if not pair:
                    pair = find_metric_pair_for_window(metric, index, lines)
                    if pair:
                        candidate_score += 1
            else:
                pair = find_metric_pair_for_window(metric, index, lines)
                if pair:
                    candidate_score += 1
            if not pair:
                pair = parse_adjacent_metric(index, lines)
            if not pair:
                continue
            if metric in {"debt", "net_profit"}:
                continue
            if line_pattern_hit:
                candidate_score += 5
            if line_fuzzy_hit:
                candidate_score += 6
            if not (line_pattern_hit or line_fuzzy_hit) and (context_pattern_hit or context_fuzzy_hit):
                candidate_score += 2
            if metric == "assets" and "pasiva celkem" in line_key:
                candidate_score += 10
            if metric == "assets" and "aktiva celkem" in line_key and len(digit_groups) >= 8:
                candidate_score -= 4
            if metric == "revenue" and any(token in line_key for token in ["trzby z prodeje vyrobk", "trzby z prodeje sluzeb"]):
                candidate_score += 4
            previous_best = found.get(metric)
            previous_score = found_scores.get(metric, -10**9)
            if previous_best is None or candidate_score > previous_score:
                found[metric] = pair
                found_scores[metric] = candidate_score

    debt_pair = extract_debt_pair(lines)
    if debt_pair:
        found["debt"] = debt_pair

    net_profit_pair = extract_net_profit_pair(lines) or extract_net_profit_from_equity_changes(lines)
    if net_profit_pair:
        found["net_profit"] = net_profit_pair

    if "equity" not in found:
        pair = extract_equity_from_statement_of_changes(lines, doc_year)
        if pair:
            found["equity"] = pair

    year_map: dict[int, dict[str, float]] = {}
    if doc_year:
        year_map[doc_year] = {}
        year_map[doc_year - 1] = {}
    for metric, pair in found.items():
        current_val = monetary_to_million_czk(pair[0], multiplier)
        previous_val = monetary_to_million_czk(pair[1], multiplier)
        if doc_year:
            year_map[doc_year][metric] = current_val
            year_map[doc_year - 1][metric] = previous_val
    return {
        "year_map": year_map,
        "multiplier": multiplier,
        "found_metrics": found,
    }


def merge_attachment_year_map(target: dict[int, dict[str, Any]], year_map: dict[int, dict[str, float]], weight: int, primary_year: int | None) -> None:
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
                combined_parts.append(f"\n\n--- ATTACHMENT {attachment_copy.get('label') or 'PDF'} ---\n{metric_text[:120000]}")
            weight = int(doc_copy.get("doc_quality_score") or 0) + int(attachment.get("candidate_score") or 0)
            best_weight = max(best_weight, weight)
            merge_attachment_year_map(merged_year_map, extracted.get("year_map") or {}, weight, primary_year)
            doc_metrics.update(found_metrics)
            if attachment_copy.get("extraction_mode"):
                modes.add(str(attachment_copy.get("extraction_mode")))
            total_pages += int(attachment_copy.get("page_count") or 0)
        except Exception as exc:
            retried = False
            refreshed_url = None
            if doc_copy.get("detail_url") and attachment_copy.get("label"):
                try:
                    refreshed_detail = parse_document_detail(str(doc_copy.get("detail_url")), force_refresh=True, parent_type=doc_copy.get("type"))
                    for refreshed in refreshed_detail.get("pdf_candidates") or []:
                        if norm_key(str(refreshed.get("label") or "")) == norm_key(str(attachment_copy.get("label") or "")):
                            refreshed_url = refreshed.get("url")
                            break
                except Exception:
                    refreshed_url = None
            if refreshed_url and refreshed_url != attachment.get("url"):
                try:
                    pdf_text = get_pdf_text(str(refreshed_url))
                    metric_text = build_metric_source_text(pdf_text)
                    extracted = extract_financial_metrics_from_text(metric_text, primary_year) if metric_text.strip() else {"year_map": {}, "found_metrics": {}}
                    found_metrics = sorted(list((extracted.get("found_metrics") or {}).keys()))
                    attachment_copy["url"] = refreshed_url
                    attachment_copy["page_count"] = pdf_text.get("page_count") or attachment.get("page_hint") or 0
                    attachment_copy["extraction_mode"] = pdf_text.get("mode")
                    attachment_copy["metrics_found"] = found_metrics
                    if metric_text.strip():
                        combined_parts.append(f"\n\n--- ATTACHMENT {attachment_copy.get('label') or 'PDF'} ---\n{metric_text[:120000]}")
                    weight = int(doc_copy.get("doc_quality_score") or 0) + int(attachment.get("candidate_score") or 0)
                    best_weight = max(best_weight, weight)
                    merge_attachment_year_map(merged_year_map, extracted.get("year_map") or {}, weight, primary_year)
                    doc_metrics.update(found_metrics)
                    if attachment_copy.get("extraction_mode"):
                        modes.add(str(attachment_copy.get("extraction_mode")))
                    total_pages += int(attachment_copy.get("page_count") or 0)
                    retried = True
                except Exception:
                    retried = False
            if not retried:
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


def merge_doc_year_map(timeline: dict[int, dict[str, Any]], doc_copy: dict[str, Any], year_map: dict[int, dict[str, float]]) -> None:
    primary_year = (doc_copy.get("years") or [None])[0]
    for year, values in year_map.items():
        slot = timeline.setdefault(year, {"year": year, "sources": []})
        doc_score = int(doc_copy.get("doc_quality_score") or 0)
        value_score = doc_score + 20 if year == primary_year else doc_score - 20
        for key, value in values.items():
            if value is None:
                continue
            existing_value = slot.get(key)
            existing_score = slot.get(f"_{key}_score", -1)
            if existing_value is None or value_score > existing_score:
                slot[key] = value
                slot[f"_{key}_score"] = value_score
        slot["sources"].append(
            {
                "document_number": doc_copy.get("document_number"),
                "detail_url": doc_copy.get("detail_url"),
                "pdf_url": doc_copy.get("pdf_url"),
                "year": primary_year,
            }
        )


def finalize_financial_timeline(timeline: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = [timeline[y] for y in sorted(timeline.keys()) if y >= 2000]
    for row in ordered:
        for key in list(row.keys()):
            if key.startswith("_") and key.endswith("_score"):
                row.pop(key, None)
    ordered = normalize_timeline_outliers(ordered)
    ordered = sanitize_financial_rows(ordered)
    ordered = recalculate_timeline_ratios(ordered)
    return ordered


def merge_financial_timeline(docs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    timeline: dict[int, dict[str, Any]] = {}
    processed_docs: list[dict[str, Any]] = []
    for doc in docs:
        doc_copy, year_map = extract_financial_doc_data(doc)
        processed_docs.append(doc_copy)
        merge_doc_year_map(timeline, doc_copy, year_map)
    ordered = finalize_financial_timeline(timeline)
    return ordered, processed_docs


def pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None:
        return None
    if abs(previous) < 1e-9:
        return None
    return round(((current - previous) / abs(previous)) * 100, 1)


def summarize_timeline(timeline: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(timeline, key=lambda x: x["year"])
    gaps: list[int] = []
    if ordered:
        years = [row["year"] for row in ordered]
        for y in range(min(years), max(years) + 1):
            if y not in years:
                gaps.append(y)
    revenue_trend = []
    profit_trend = []
    latest = ordered[-1] if ordered else None
    previous = ordered[-2] if len(ordered) >= 2 else None
    for idx in range(1, len(ordered)):
        curr = ordered[idx]
        prev = ordered[idx - 1]
        revenue_trend.append({"year": curr["year"], "change": pct_change(curr.get("revenue"), prev.get("revenue"))})
        profit_trend.append({"year": curr["year"], "change": pct_change(curr.get("net_profit"), prev.get("net_profit"))})
    return {
        "latest": latest,
        "previous": previous,
        "missing_years": gaps,
        "revenue_trend": revenue_trend,
        "profit_trend": profit_trend,
    }


def extract_history_events(full_extract: dict[str, Any]) -> dict[str, Any]:
    rows = full_extract.get("rows", [])
    name_changes = 0
    address_changes = 0
    management_turnover = 0
    for row in rows:
        label = norm_key(row.get("label", ""))
        history = norm_key(row.get("history", ""))
        if label == "obchodni firma" and "vymazano" in history:
            name_changes += 1
        if label == "sidlo" and "vymazano" in history:
            address_changes += 1
        if any(k in label for k in ["predseda predstavenstva", "clen predstavenstva", "jednatel", "prokurista"]):
            if "vymazano" in history or "zaniku" in norm_key(row.get("value", "")):
                management_turnover += 1
    return {
        "name_changes": name_changes,
        "address_changes": address_changes,
        "management_turnover": management_turnover,
    }


def parse_person_text(role: str | None, text: str) -> dict[str, Any]:
    clean = norm_text(text)
    parts = [p.strip() for p in clean.split(" Den vzniku")]
    head = parts[0]
    name = re.split(r",\s*dat\.\s*nar\.| dat\.\s*nar\.", head, maxsplit=1, flags=re.I)[0].strip(" ,")
    return {
        "role": role,
        "name": name,
        "birth_date": extract_birth_date(clean),
        "raw": clean,
    }


def extract_people_and_owners(current_extract: dict[str, Any]) -> dict[str, Any]:
    executives: list[dict[str, Any]] = []
    owners: list[dict[str, Any]] = []
    bodies: list[dict[str, Any]] = []
    for section in current_extract.get("sections", []):
        title = section.get("title", "")
        title_key = norm_key(title)
        body = {"title": title, "items": section.get("items", [])}
        if section.get("items"):
            bodies.append(body)
        if any(k in title_key for k in ["statutarni", "predstavenstvo", "jednatel", "dozorci rada", "spravni rada", "prokurista"]):
            for item in section.get("items", []):
                if item.get("text"):
                    executives.append(parse_person_text(best_role_for_section(title, item.get("role")), item.get("text")))
        if any(k in title_key for k in ["spolecnik", "spolecnici", "akcionar", "jediny akcionar", "akcie"]):
            for item in section.get("items", []):
                if not item.get("text"):
                    continue
                if not owner_item_is_primary(item.get("role"), item.get("text")):
                    continue
                owners.append({
                    "role": best_role_for_section(title, item.get("role")),
                    "name": extract_owner_name(item.get("text")),
                    "raw": item.get("text"),
                })
    return {
        "executives": dedupe_people(executives),
        "owners": dedupe_people(owners, key_name="name"),
        "bodies": bodies,
    }


def build_highlights(timeline: list[dict[str, Any]], docs: list[dict[str, Any]], history: dict[str, Any]) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    overview: list[dict[str, str]] = []
    deep: list[dict[str, str]] = []
    praskac: list[dict[str, str]] = []
    summary = summarize_timeline(timeline)
    latest = summary.get("latest")
    previous = summary.get("previous")

    if latest:
        if latest.get("revenue") is not None and previous:
            growth = pct_change(latest.get("revenue"), previous.get("revenue"))
            if growth is not None:
                tone = "Růst" if growth > 0 else "Pokles"
                overview.append({
                    "title": f"{tone} tržeb",
                    "detail": f"Mezi roky {previous['year']} a {latest['year']} se tržby změnily o {growth} %.",
                })
        if latest.get("net_margin_pct") is not None:
            overview.append({
                "title": "Ziskovost",
                "detail": f"Čistá marže za rok {latest['year']} vychází na {latest['net_margin_pct']} %.",
            })
        if latest.get("equity_ratio_pct") is not None:
            deep.append({
                "title": "Kapitálová síla",
                "detail": f"Podíl vlastního kapitálu na aktivech je v roce {latest['year']} {latest['equity_ratio_pct']} %.",
            })
        if latest.get("liability_ratio_pct") is not None:
            if latest["liability_ratio_pct"] >= 80:
                praskac.append({
                    "title": "Vysoká závislost na cizích zdrojích",
                    "detail": f"Cizí zdroje tvoří v roce {latest['year']} asi {latest['liability_ratio_pct']} % aktiv.",
                })
            else:
                deep.append({
                    "title": "Zatížení závazky",
                    "detail": f"Cizí zdroje tvoří v roce {latest['year']} asi {latest['liability_ratio_pct']} % aktiv.",
                })
        if latest.get("net_profit") is not None and latest["net_profit"] < 0:
            praskac.append({
                "title": "Firma je ve ztrátě",
                "detail": f"Za rok {latest['year']} vychází čistý výsledek záporně ({format_million(latest['net_profit'])}).",
            })

    negative_years = [row["year"] for row in timeline if row.get("net_profit") is not None and row["net_profit"] < 0]
    if len(negative_years) >= 2:
        praskac.append({
            "title": "Opakované ztrátové roky",
            "detail": f"Záporný čistý výsledek je vidět ve více letech: {', '.join(map(str, negative_years[-4:]))}.",
        })

    if summary.get("missing_years"):
        years_txt = ", ".join(str(y) for y in summary["missing_years"][:8])
        overview.append({
            "title": "Chybějící roky",
            "detail": f"Ve vybraných finančních podkladech chybí roky: {years_txt}.",
        })
        if len(summary["missing_years"]) >= 2:
            praskac.append({
                "title": "Díry ve Sbírce listin",
                "detail": f"Ve vybraném časovém řetězci chybí více let: {years_txt}.",
            })

    for doc in docs:
        primary_year = (doc.get("years") or [None])[0]
        if not primary_year:
            continue
        year_end = f"{primary_year}-12-31"
        delay = days_between(year_end, doc.get("filed_date"))
        if delay and delay > 365:
            praskac.append({
                "title": f"Pozdní založení podkladů za {primary_year}",
                "detail": f"Dokument byl do Sbírky listin založen přibližně {delay} dní po konci roku.",
            })
        elif delay and delay > 240:
            deep.append({
                "title": f"Pomalejší založení podkladů za {primary_year}",
                "detail": f"Dokument byl do Sbírky listin založen asi {delay} dní po konci roku.",
            })

    if history.get("name_changes"):
        deep.append({
            "title": "Změny názvu",
            "detail": f"V historickém výpisu je vidět {history['name_changes']} dřívějších změn obchodní firmy.",
        })
    if history.get("address_changes"):
        deep.append({
            "title": "Změny sídla",
            "detail": f"V historickém výpisu je vidět {history['address_changes']} změn sídla nebo formátu adresy.",
        })
    if history.get("management_turnover", 0) >= 8:
        praskac.append({
            "title": "Vyšší personální obměna ve vedení",
            "detail": f"V úplném výpisu je zachyceno hodně změn ve statutárních funkcích ({history['management_turnover']}).",
        })
    elif history.get("management_turnover", 0) >= 3:
        deep.append({
            "title": "Obměna ve vedení",
            "detail": f"Úplný výpis zachycuje více změn ve statutárních funkcích ({history['management_turnover']}).",
        })

    if not overview:
        overview.append({
            "title": "Málo strojově čitelných dat",
            "detail": "Ve veřejných podkladech se nepodařilo spolehlivě vytěžit dost finančních metrik. Odkazy na zdroje jsou ale níže.",
        })
    if not deep:
        deep.append({
            "title": "Bez výraznějšího vzorce",
            "detail": "Z dostupných podkladů není bez dalších zdrojů vidět silně neobvyklý trend, jen standardní veřejné údaje z rejstříku.",
        })
    if not praskac:
        praskac.append({
            "title": "Nic extra křiklavého",
            "detail": "V samotném justice.cz není zjevný varovný signál, který by šel bez spekulací označit jako problém. Ber to jen jako rychlý screening z veřejných záznamů.",
        })
    return overview[:6], deep[:8], praskac[:8]


def format_million(value: float | None) -> str:
    if value is None:
        return "—"
    abs_value = abs(value)
    if abs_value >= 1000:
        return f"{value:,.1f} mil. Kč".replace(",", " ")
    return f"{value:,.2f} mil. Kč".replace(",", " ")


def extract_json_block(raw_text: str) -> dict[str, Any]:
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        raise ValueError("AI response did not contain JSON object")
    return json.loads(match.group(0))


def compact_people_for_ai(items: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for item in items[:limit]:
        compact.append({
            "role": item.get("role"),
            "name": item.get("name"),
            "raw": item.get("raw"),
        })
    return compact


def compact_docs_for_ai(docs: list[dict[str, Any]], limit: int = 6) -> list[dict[str, Any]]:
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


def compact_timeline_for_ai(timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keep = [
        "year",
        "revenue",
        "operating_profit",
        "net_profit",
        "assets",
        "equity",
        "liabilities",
        "debt",
        "net_margin_pct",
        "equity_ratio_pct",
        "liability_ratio_pct",
        "debt_to_revenue_pct",
    ]
    compact: list[dict[str, Any]] = []
    for row in timeline[-6:]:
        compact.append({key: row.get(key) for key in keep if key == "year" or row.get(key) is not None})
    return compact


def clean_ai_items(items: Any, fallback: list[dict[str, str]], limit: int) -> list[dict[str, str]]:
    cleaned: list[dict[str, str]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        title = norm_text(str(item.get("title") or ""))
        detail = norm_text(str(item.get("detail") or ""))
        if title and detail:
            cleaned.append({"title": title[:120], "detail": detail[:420]})
    return cleaned[:limit] or fallback[:limit]


def generate_ai_analysis(
    company_name: str,
    ico: str,
    basic_info_items: list[dict[str, str]],
    executives: list[dict[str, Any]],
    owners: list[dict[str, Any]],
    history: dict[str, Any],
    timeline: list[dict[str, Any]],
    docs: list[dict[str, Any]],
    overview_fallback: list[dict[str, str]],
    deep_fallback: list[dict[str, str]],
    praskac_fallback: list[dict[str, str]],
) -> dict[str, Any]:
    payload = {
        "company_name": company_name,
        "ico": ico,
        "basic_info": basic_info_items,
        "executives": compact_people_for_ai(executives),
        "owners": compact_people_for_ai(owners),
        "history_signals": history,
        "financial_timeline": compact_timeline_for_ai(timeline),
        "documents": compact_docs_for_ai(docs),
        "fallback_signals": {
            "summary": overview_fallback,
            "deep": deep_fallback,
            "praskac": praskac_fallback,
        },
    }
    prompt = f"""
Jsi analytik českých firem. Pracuj jen s dodanými veřejnými podklady z justice.cz a Sbírky listin.

Pravidla:
- Nepřidávej nic, co není podloženo daty v payloadu.
- Když jsou data slabá nebo neúplná, řekni to přímo.
- Piš česky, stručně, věcně a prakticky.
- Sekce Práskač má být přímočará, ale pořád faktická a bez nepodložených obvinění.
- Hledej trendy v růstu, poklesu, ziskovosti, zadlužení, kapitálu, chybějících letech, pozdních listinách a změnách ve vedení.
- Pokud nejsou jasné finanční závěry, přiznej omezení místo spekulace.

Vrať pouze JSON v tomto tvaru:
{{
  "analysis_overview": "2-4 věty shrnutí v jedné krátké odstavcové pasáži",
  "data_quality_note": "jedna věta o kvalitě a limitech dat",
  "insight_summary": [{{"title": "...", "detail": "..."}}],
  "deep_insights": [{{"title": "...", "detail": "..."}}],
  "praskac": [{{"title": "...", "detail": "..."}}]
}}

Payload:
{json.dumps(payload, ensure_ascii=False, indent=2)}
"""
    client = Anthropic(timeout=AI_TIMEOUT_SECONDS)
    response = client.messages.create(
        model=AI_MODEL,
        max_tokens=1800,
        temperature=0.2,
        messages=[{"role": "user", "content": prompt}],
    )
    usage = getattr(response, "usage", None)
    usage_payload = {
        "provider": "anthropic",
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", None),
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", None),
        "credits": None,
        "credits_note": "Přesné kredity nejsou z API dostupné.",
    }
    text = "\n".join(block.text for block in response.content if getattr(block, "type", None) == "text")
    parsed = extract_json_block(text)
    return {
        "analysis_engine": "ai",
        "analysis_model": AI_MODEL,
        "analysis_usage": usage_payload,
        "analysis_overview": norm_text(str(parsed.get("analysis_overview") or "")) or "AI rozbor z veřejných listin není k dispozici.",
        "data_quality_note": norm_text(str(parsed.get("data_quality_note") or "")) or "Kvalita dat závisí na čitelnosti veřejných PDF a úplnosti Sbírky listin.",
        "insight_summary": clean_ai_items(parsed.get("insight_summary"), overview_fallback, 6),
        "deep_insights": clean_ai_items(parsed.get("deep_insights"), deep_fallback, 8),
        "praskac": clean_ai_items(parsed.get("praskac"), praskac_fallback, 8),
    }


def build_basic_info(current_extract: dict[str, Any]) -> list[dict[str, str]]:
    info = current_extract.get("basic_info", {})
    ordered_keys = [
        "Obchodní firma",
        "Identifikační číslo",
        "Právní forma",
        "Datum vzniku a zápisu",
        "Spisová značka",
        "Sídlo",
    ]
    items = []
    for key in ordered_keys:
        if info.get(key):
            items.append({"label": key, "value": info[key]})
    for extra_key in ["Předmět podnikání", "Základní kapitál"]:
        if info.get(extra_key):
            items.append({"label": extra_key, "value": info[extra_key]})
    return items


def company_slug(value: str) -> str:
    slug = strip_accents(value or "").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug


def fetch_chytryrejstrik_snapshot(company_name: str, ico: str) -> dict[str, Any] | None:
    ico_clean = clean_ico(ico)
    slug = company_slug(company_name)
    if not ico_clean or not slug:
        return None
    url = f"https://www.chytryrejstrik.cz/ico-{ico_clean}/{slug}"
    try:
        response = SESSION.get(url, timeout=30)
        if response.status_code != 200:
            return None
        html = response.text
    except Exception:
        return None

    def parse_money(label: str) -> float | None:
        pattern = re.compile(rf">\s*{re.escape(label)}\s*<.*?>\s*<span[^>]*>\s*([0-9\s]+)\s*Kč", re.I | re.S)
        match = pattern.search(html)
        if not match:
            return None
        raw = re.sub(r"\s+", "", match.group(1))
        try:
            return round(int(raw) / 1_000_000, 2)
        except Exception:
            return None

    snapshot = {
        "url": url,
        "assets_mil_czk": parse_money("Aktiva"),
        "profit_mil_czk": parse_money("Zisk"),
        "employees_hint": None,
    }
    emp_match = re.search(r">\s*Počet zaměstnanců\s*<.*?>\s*<span[^>]*>\s*([^<]+)<", html, re.I | re.S)
    if emp_match:
        snapshot["employees_hint"] = norm_text(emp_match.group(1))
    if not any(snapshot.get(key) is not None for key in ["assets_mil_czk", "profit_mil_czk", "employees_hint"]):
        return None
    return snapshot


def build_external_checks(timeline: list[dict[str, Any]], company_name: str, ico: str) -> dict[str, Any] | None:
    snapshot = fetch_chytryrejstrik_snapshot(company_name, ico)
    if not snapshot:
        return None
    latest = timeline[-1] if timeline else {}
    checks: list[dict[str, Any]] = []
    assets = latest.get("assets")
    if assets is not None and snapshot.get("assets_mil_czk") is not None:
        diff = round(assets - snapshot["assets_mil_czk"], 2)
        checks.append(
            {
                "label": "Aktiva vs. Chytrý rejstřík",
                "status": "ok" if abs(diff) <= 2 else "warning",
                "app_value": assets,
                "external_value": snapshot["assets_mil_czk"],
                "detail": "Rozdíl do 2 mil. Kč beru jako přijatelný kvůli zaokrouhlení." if abs(diff) <= 2 else "Hodnoty se rozcházejí, chce to ruční kontrolu PDF.",
            }
        )
    profit = latest.get("net_profit")
    if profit is not None and snapshot.get("profit_mil_czk") is not None:
        diff = round(profit - snapshot["profit_mil_czk"], 2)
        checks.append(
            {
                "label": "Zisk vs. Chytrý rejstřík",
                "status": "ok" if abs(diff) <= 2 else "warning",
                "app_value": profit,
                "external_value": snapshot["profit_mil_czk"],
                "detail": "Rozdíl do 2 mil. Kč beru jako přijatelný kvůli zaokrouhlení." if abs(diff) <= 2 else "Hodnoty se rozcházejí, chce to ruční kontrolu PDF.",
            }
        )
    return {
        "source_name": "Chytrý rejstřík",
        "source_url": snapshot["url"],
        "employees_hint": snapshot.get("employees_hint"),
        "checks": checks,
        "snapshot": snapshot,
    }


def build_company_profile(subjekt_id: str, visitor_id: str | None = None, query: str | None = None, force_refresh: bool = False) -> dict[str, Any]:
    cache_name = f"company_profile_{PROFILE_CACHE_VERSION}_{subjekt_id}"
    if not force_refresh:
        cached = load_json_cache(cache_name, PROFILE_CACHE_TTL_SECONDS)
        if cached is not None:
            cached["cache_status"] = "cached"
            save_history_entry(visitor_id, cached, query=query)
            return cached

    current_extract = fetch_extract(subjekt_id, "PLATNY", force_refresh=force_refresh)
    full_extract = fetch_extract(subjekt_id, "UPLNY", force_refresh=force_refresh)
    docs = parse_document_list(subjekt_id, force_refresh=force_refresh)
    relevant_docs = pick_recent_financial_docs(docs, max_years=5, force_refresh_details=force_refresh)
    timeline, processed_docs = merge_financial_timeline(relevant_docs)
    people = extract_people_and_owners(current_extract)
    history = extract_history_events(full_extract)
    overview, deep, praskac = build_highlights(timeline, processed_docs, history)

    basic_info_items = build_basic_info(current_extract)
    company_name = current_extract.get("basic_info", {}).get("Obchodní firma") or current_extract.get("subtitle") or "Společnost"
    ico = clean_ico(str(current_extract.get("basic_info", {}).get("Identifikační číslo", "")))

    ai_analysis: dict[str, Any]
    if AI_ENABLED:
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
            ai_analysis = {
                "analysis_engine": "fallback",
                "analysis_model": None,
                "analysis_usage": None,
                "analysis_overview": "Shrnutí běží bez AI vrstvy. Níže je pravidlový výstup z veřejných podkladů justice.cz.",
                "data_quality_note": "Kvalita dat závisí na čitelnosti veřejných PDF a úplnosti Sbírky listin.",
                "insight_summary": overview,
                "deep_insights": deep,
                "praskac": praskac,
            }
    else:
        ai_analysis = {
            "analysis_engine": "disabled",
            "analysis_model": None,
            "analysis_usage": None,
            "analysis_overview": "AI vrstva je vypnutá. Níže je pravidlový výstup z veřejných podkladů justice.cz.",
            "data_quality_note": "Kvalita dat závisí na čitelnosti veřejných PDF a úplnosti Sbírky listin.",
            "insight_summary": overview,
            "deep_insights": deep,
            "praskac": praskac,
        }

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
        "cache_status": "fresh" if force_refresh else "fresh",
    }
    save_json_cache(cache_name, profile)
    save_history_entry(visitor_id, profile, query=query)
    return profile


app = FastAPI(title="Justice Práskač API")

_cors_origins_env = os.environ.get("JUSTICE_CORS_ORIGINS", "http://localhost:3000")
_cors_origins = [o.strip() for o in _cors_origins_env.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["Accept", "Content-Type"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/search")
def api_search(q: str = Query(..., min_length=2)) -> dict[str, Any]:
    results = search_companies(q)
    return {"query": q, "count": len(results), "results": results}


@app.get("/api/history")
def api_history(request: Request) -> dict[str, Any]:
    visitor_id = request.headers.get("X-Visitor-Id")
    return {"items": get_history_entries(visitor_id)}


@app.get("/api/company")
def api_company(request: Request, subjekt_id: str = Query(..., alias="subjektId"), q: str | None = Query(None), refresh: bool = Query(False)) -> dict[str, Any]:
    if not subjekt_id or not subjekt_id.strip().isdigit():
        raise HTTPException(status_code=400, detail="Neplatné ID subjektu.")
    visitor_id = request.headers.get("X-Visitor-Id")
    try:
        profile = build_company_profile(subjekt_id, visitor_id=visitor_id, query=q, force_refresh=refresh)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=public_error_message(exc)) from exc
    return profile


def inline_pdf_filename(label: str | None, index: int) -> str:
    raw = norm_text(label or f"listina-{index + 1}.pdf")
    safe = re.sub(r'[^A-Za-z0-9._-]+', '-', strip_accents(raw)).strip('-') or f"listina-{index + 1}.pdf"
    if not safe.lower().endswith('.pdf'):
        safe += '.pdf'
    return safe


@app.get("/api/document/resolve")
def api_document_resolve(detail_url: str = Query(..., alias="detailUrl"), index: int = Query(0, ge=0), prefer_pdf: bool = Query(True)) -> FileResponse:
    allowed_prefixes = ("https://or.justice.cz/",)
    if not any(detail_url.startswith(p) for p in allowed_prefixes):
        raise HTTPException(status_code=400, detail="Neplatná URL dokumentu.")
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


@app.get("/api/company/stream")
def api_company_stream(request: Request, subjekt_id: str = Query(..., alias="subjektId"), q: str | None = Query(None), refresh: bool = Query(False)) -> StreamingResponse:
    if not subjekt_id or not subjekt_id.strip().isdigit():
        raise HTTPException(status_code=400, detail="Neplatné ID subjektu.")
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
                    yield sse_event("status", {"label": "Načítám uložený profil z mezipaměti"})
                    yield sse_event("result", cached)
                    return

            yield sse_event("status", {"label": "Spouštím novou extrakci z veřejných podkladů" if refresh else "Otevírám aktuální výpis firmy"})
            current_extract = fetch_extract(subjekt_id, "PLATNY", force_refresh=refresh)
            basic_info_items = build_basic_info(current_extract)
            company_name = current_extract.get("basic_info", {}).get("Obchodní firma") or current_extract.get("subtitle") or "Společnost"
            ico = clean_ico(str(current_extract.get("basic_info", {}).get("Identifikační číslo", "")))
            yield sse_event("preview", {"subject_id": subjekt_id, "name": company_name, "ico": ico, "basic_info": basic_info_items})

            yield sse_event("status", {"label": "Čtu úplný výpis a historii změn"})
            full_extract = fetch_extract(subjekt_id, "UPLNY", force_refresh=refresh)
            people = extract_people_and_owners(current_extract)
            history = extract_history_events(full_extract)

            yield sse_event("status", {"label": "Stahuji seznam listin ze Sbírky listin"})
            docs = parse_document_list(subjekt_id, force_refresh=refresh)
            relevant_docs = pick_recent_financial_docs(docs, max_years=5, force_refresh_details=refresh)
            yield sse_event("status", {"label": f"Vybral jsem {len(relevant_docs)} relevantních listin a projdu všechny kandidátní PDF přílohy"})

            timeline_map: dict[int, dict[str, Any]] = {}
            processed_docs: list[dict[str, Any]] = []
            total_docs = len(relevant_docs)
            for idx, doc in enumerate(relevant_docs, start=1):
                year_hint = (doc.get("years") or [None])[0]
                doc_title = doc.get("document_number") or doc.get("type") or "listina"
                candidate_count = len(doc.get("pdf_candidates") or [])
                if year_hint:
                    label = f"Čtu listinu {idx}/{total_docs}: {doc_title} · rok {year_hint} · soubory {candidate_count}"
                else:
                    label = f"Čtu listinu {idx}/{total_docs}: {doc_title} · soubory {candidate_count}"
                yield sse_event("status", {"label": label})
                doc_copy, year_map = extract_financial_doc_data(doc)
                processed_docs.append(doc_copy)
                merge_doc_year_map(timeline_map, doc_copy, year_map)
                metric_count = len(doc_copy.get("metrics_found") or [])
                if metric_count:
                    yield sse_event("status", {"label": f"Z listiny {idx}/{total_docs} jsem vytáhl {metric_count} metrik"})
                else:
                    yield sse_event("status", {"label": f"Listina {idx}/{total_docs} má slabší čitelnost, zkouším další podklady"})

            timeline = finalize_financial_timeline(timeline_map)
            overview, deep, praskac = build_highlights(timeline, processed_docs, history)

            yield sse_event("status", {"label": "Kontroluji trendy, díry v letech a veřejné signály"})
            if AI_ENABLED:
                yield sse_event("status", {"label": "Píšu AI shrnutí a skládám body do profilu"})
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
                    ai_analysis = {
                        "analysis_engine": "fallback",
                        "analysis_model": None,
                        "analysis_usage": None,
                        "analysis_overview": "Shrnutí běží bez AI vrstvy. Níže je pravidlový výstup z veřejných podkladů justice.cz.",
                        "data_quality_note": "Kvalita dat závisí na čitelnosti veřejných PDF a úplnosti Sbírky listin.",
                        "insight_summary": overview,
                        "deep_insights": deep,
                        "praskac": praskac,
                    }
            else:
                ai_analysis = {
                    "analysis_engine": "disabled",
                    "analysis_model": None,
                    "analysis_usage": None,
                    "analysis_overview": "AI vrstva je vypnutá. Níže je pravidlový výstup z veřejných podkladů justice.cz.",
                    "data_quality_note": "Kvalita dat závisí na čitelnosti veřejných PDF a úplnosti Sbírky listin.",
                    "insight_summary": overview,
                    "deep_insights": deep,
                    "praskac": praskac,
                }

            yield sse_event("status", {"label": "Porovnávám čísla s veřejnou kontrolou a ukládám sdílenou historii"})
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
            yield sse_event("error", {"detail": public_error_message(exc)})

    return StreamingResponse(iterator(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
