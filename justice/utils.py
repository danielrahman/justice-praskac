from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import requests


# ---------------------------------------------------------------------------
# Structured JSON logging
# ---------------------------------------------------------------------------
class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)


_log_level = os.environ.get("JUSTICE_LOG_LEVEL", "INFO").upper()
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(JsonFormatter())
logging.basicConfig(level=getattr(logging, _log_level, logging.INFO), handlers=[_handler])
logger = logging.getLogger("justice")


BASE_UI = "https://or.justice.cz/ias/ui/"
BASE_SITE = "https://or.justice.cz"
ROOT_DIR = Path(__file__).resolve().parent.parent
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
        logger.info(f"cache miss name={name}")
        return None
    if now_ts() - path.stat().st_mtime > max_age_seconds:
        logger.info(f"cache miss (expired) name={name}")
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        logger.info(f"cache hit name={name}")
        return data
    except Exception:
        logger.info(f"cache miss (read error) name={name}")
        return None


def save_json_cache(name: str, data: Any) -> None:
    path = JSON_DIR / f"{name}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"cache write name={name}")


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
    for ch in "|[](){}:,;~=""'''\"":
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


def parse_loose_number(raw: str) -> int | None:
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return None
    try:
        return int(digits)
    except Exception:
        return None


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
