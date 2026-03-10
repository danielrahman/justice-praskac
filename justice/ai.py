from __future__ import annotations

import json
import re
import time
from datetime import datetime
from typing import Any

from anthropic import Anthropic

from justice.db import save_history_entry
from justice.extraction import (
    pct_change,
    summarize_timeline,
)
from justice.scraping import (
    SESSION,
    clean_ico,
)
from justice.utils import (
    ANTHROPIC_API_KEY,
    AI_ENABLED,
    AI_MODEL,
    AI_TIMEOUT_SECONDS,
    logger,
    norm_key,
    norm_text,
    strip_accents,
)


ANTHROPIC_MODEL_PRICING: tuple[tuple[re.Pattern[str], dict[str, float | str]], ...] = (
    (
        re.compile(r"^claude-opus-4-6(?:-|$)"),
        {
            "pricing_model": "claude-opus-4.6",
            "input_usd_per_million": 5.0,
            "output_usd_per_million": 25.0,
            "cache_write_usd_per_million": 6.25,
            "cache_read_usd_per_million": 0.5,
        },
    ),
    (
        re.compile(r"^claude-opus-4(?:-|$)"),
        {
            "pricing_model": "claude-opus-4.1",
            "input_usd_per_million": 15.0,
            "output_usd_per_million": 75.0,
            "cache_write_usd_per_million": 18.75,
            "cache_read_usd_per_million": 1.5,
        },
    ),
    (
        re.compile(r"^claude-sonnet-4(?:-|$)"),
        {
            "pricing_model": "claude-sonnet-4.5",
            "input_usd_per_million": 3.0,
            "output_usd_per_million": 15.0,
            "cache_write_usd_per_million": 3.75,
            "cache_read_usd_per_million": 0.3,
        },
    ),
    (
        re.compile(r"^claude-haiku-(?:4|3-5)(?:-|$)"),
        {
            "pricing_model": "claude-haiku-4.5",
            "input_usd_per_million": 1.0,
            "output_usd_per_million": 5.0,
            "cache_write_usd_per_million": 1.25,
            "cache_read_usd_per_million": 0.1,
        },
    ),
)


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


def extract_history_events(full_extract: dict[str, Any]) -> dict[str, Any]:
    rows = full_extract.get("rows", [])
    name_changes = 0
    address_changes = 0
    management_turnover = 0
    current_management_role = ""
    management_labels = ["predseda predstavenstva", "clen predstavenstva", "jednatel", "prokurista"]
    for row in rows:
        label = norm_key(row.get("label", ""))
        history = norm_key(row.get("history", ""))
        value = norm_key(row.get("value", ""))
        if any(key in label for key in management_labels):
            current_management_role = label
        elif label:
            current_management_role = ""
        effective_label = label or current_management_role
        if label == "obchodni firma" and "vymazano" in history:
            name_changes += 1
        if label == "sidlo" and "vymazano" in history:
            address_changes += 1
        if any(k in effective_label for k in management_labels):
            if "vymazano" in history or "zaniku" in value:
                management_turnover += 1
    return {
        "name_changes": name_changes,
        "address_changes": address_changes,
        "management_turnover": management_turnover,
    }


def format_million(value: float | None) -> str:
    if value is None:
        return "\u2014"
    abs_value = abs(value)
    if abs_value >= 1000:
        return f"{value:,.1f} mil. K\u010d".replace(",", " ")
    return f"{value:,.2f} mil. K\u010d".replace(",", " ")


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
                tone = "R\u016fst" if growth > 0 else "Pokles"
                overview.append({
                    "title": f"{tone} tr\u017eeb",
                    "detail": f"Mezi roky {previous['year']} a {latest['year']} se tr\u017eby zm\u011bnily o {growth} %.",
                })
        if latest.get("net_margin_pct") is not None:
            overview.append({
                "title": "Ziskovost",
                "detail": f"\u010cist\u00e1 mar\u017ee za rok {latest['year']} vych\u00e1z\u00ed na {latest['net_margin_pct']} %.",
            })
        if latest.get("equity_ratio_pct") is not None:
            deep.append({
                "title": "Kapit\u00e1lov\u00e1 s\u00edla",
                "detail": f"Pod\u00edl vlastn\u00edho kapit\u00e1lu na aktivech je v roce {latest['year']} {latest['equity_ratio_pct']} %.",
            })
        if latest.get("liability_ratio_pct") is not None:
            if latest["liability_ratio_pct"] >= 80:
                praskac.append({
                    "title": "Vysok\u00e1 z\u00e1vislost na ciz\u00edch zdroj\u00edch",
                    "detail": f"Ciz\u00ed zdroje tvo\u0159\u00ed v roce {latest['year']} asi {latest['liability_ratio_pct']} % aktiv.",
                })
            else:
                deep.append({
                    "title": "Zat\u00ed\u017een\u00ed z\u00e1vazky",
                    "detail": f"Ciz\u00ed zdroje tvo\u0159\u00ed v roce {latest['year']} asi {latest['liability_ratio_pct']} % aktiv.",
                })
        if latest.get("net_profit") is not None and latest["net_profit"] < 0:
            praskac.append({
                "title": "Firma je ve ztr\u00e1t\u011b",
                "detail": f"Za rok {latest['year']} vych\u00e1z\u00ed \u010dist\u00fd v\u00fdsledek z\u00e1porn\u011b ({format_million(latest['net_profit'])}).",
            })

    negative_years = [row["year"] for row in timeline if row.get("net_profit") is not None and row["net_profit"] < 0]
    if len(negative_years) >= 2:
        praskac.append({
            "title": "Opakovan\u00e9 ztr\u00e1tov\u00e9 roky",
            "detail": f"Z\u00e1porn\u00fd \u010dist\u00fd v\u00fdsledek je vid\u011bt ve v\u00edce letech: {', '.join(map(str, negative_years[-4:]))}.",
        })

    if summary.get("missing_years"):
        years_txt = ", ".join(str(y) for y in summary["missing_years"][:8])
        overview.append({
            "title": "Chyb\u011bj\u00edc\u00ed roky",
            "detail": f"Ve vybran\u00fdch finan\u010dn\u00edch podkladech chyb\u00ed roky: {years_txt}.",
        })
        if len(summary["missing_years"]) >= 2:
            praskac.append({
                "title": "D\u00edry ve Sb\u00edrce listin",
                "detail": f"Ve vybran\u00e9m \u010dasov\u00e9m \u0159et\u011bzci chyb\u00ed v\u00edce let: {years_txt}.",
            })

    if history.get("name_changes"):
        deep.append({
            "title": "Zm\u011bny n\u00e1zvu",
            "detail": f"V historick\u00e9m v\u00fdpisu je vid\u011bt {history['name_changes']} d\u0159\u00edv\u011bj\u0161\u00edch zm\u011bn obchodn\u00ed firmy.",
        })
    if history.get("address_changes"):
        deep.append({
            "title": "Zm\u011bny s\u00eddla",
            "detail": f"V historick\u00e9m v\u00fdpisu je vid\u011bt {history['address_changes']} zm\u011bn s\u00eddla nebo form\u00e1tu adresy.",
        })
    if history.get("management_turnover", 0) >= 8:
        praskac.append({
            "title": "Vy\u0161\u0161\u00ed person\u00e1ln\u00ed obm\u011bna ve veden\u00ed",
            "detail": f"V \u00fapln\u00e9m v\u00fdpisu je zachyceno hodn\u011b zm\u011bn ve statut\u00e1rn\u00edch funkc\u00edch ({history['management_turnover']}).",
        })
    elif history.get("management_turnover", 0) >= 3:
        deep.append({
            "title": "Obm\u011bna ve veden\u00ed",
            "detail": f"\u00dapln\u00fd v\u00fdpis zachycuje v\u00edce zm\u011bn ve statut\u00e1rn\u00edch funkc\u00edch ({history['management_turnover']}).",
        })

    if not overview:
        overview.append({
            "title": "M\u00e1lo strojov\u011b \u010diteln\u00fdch dat",
            "detail": "Ve ve\u0159ejn\u00fdch podkladech se nepoda\u0159ilo spolehliv\u011b vyt\u011b\u017eit dost finan\u010dn\u00edch metrik. Odkazy na zdroje jsou ale n\u00ed\u017ee.",
        })
    if not deep:
        deep.append({
            "title": "Bez v\u00fdrazn\u011bj\u0161\u00edho vzorce",
            "detail": "Z dostupn\u00fdch podklad\u016f nen\u00ed bez dal\u0161\u00edch zdroj\u016f vid\u011bt siln\u011b neobvykl\u00fd trend, jen standardn\u00ed ve\u0159ejn\u00e9 \u00fadaje z rejst\u0159\u00edku.",
        })
    if not praskac:
        praskac.append({
            "title": "Nic extra k\u0159iklav\u00e9ho",
            "detail": "V samotn\u00e9m justice.cz nen\u00ed zjevn\u00fd varovn\u00fd sign\u00e1l, kter\u00fd by \u0161el bez spekulac\u00ed ozna\u010dit jako probl\u00e9m. Ber to jen jako rychl\u00fd screening z ve\u0159ejn\u00fdch z\u00e1znam\u016f.",
        })
    return overview[:6], deep[:8], praskac[:8]


def extract_json_block(raw_text: str) -> dict[str, Any]:
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    candidates: list[str] = []
    if text.startswith("{") and text.endswith("}"):
        candidates.append(text)

    start = text.find("{")
    if start >= 0:
        depth = 0
        in_string = False
        escaped = False
        for idx in range(start, len(text)):
            ch = text[idx]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start:idx + 1])
                    break

    if not candidates:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if match:
            candidates.append(match.group(0))

    def _attempt_parse(candidate: str) -> dict[str, Any]:
        normalized = candidate.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")
        try:
            return json.loads(normalized)
        except json.JSONDecodeError:
            normalized = re.sub(r",(\s*[}\]])", r"\1", normalized)
            return json.loads(normalized)

    for candidate in candidates:
        try:
            return _attempt_parse(candidate)
        except Exception:
            continue
    raise ValueError("AI response did not contain valid JSON object")


def get_anthropic_model_pricing(model: str | None) -> dict[str, Any] | None:
    raw = (model or "").strip().lower()
    if not raw:
        return None
    for pattern, pricing in ANTHROPIC_MODEL_PRICING:
        if pattern.search(raw):
            return dict(pricing)
    return None


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def estimate_ai_cost_usd(
    model: str | None,
    *,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cache_creation_input_tokens: int | None = None,
    cache_read_input_tokens: int | None = None,
) -> dict[str, Any] | None:
    pricing = get_anthropic_model_pricing(model)
    if not pricing:
        return None

    def _component(tokens: int | None, usd_per_million: float) -> float:
        if not tokens:
            return 0.0
        return float(tokens) / 1_000_000 * usd_per_million

    input_cost = _component(input_tokens, float(pricing["input_usd_per_million"]))
    output_cost = _component(output_tokens, float(pricing["output_usd_per_million"]))
    cache_write_cost = _component(cache_creation_input_tokens, float(pricing["cache_write_usd_per_million"]))
    cache_read_cost = _component(cache_read_input_tokens, float(pricing["cache_read_usd_per_million"]))
    total_cost = round(input_cost + output_cost + cache_write_cost + cache_read_cost, 6)
    return {
        "pricing_model": pricing["pricing_model"],
        "estimated_cost_usd": total_cost,
        "estimated_cost_breakdown": {
            "input_usd": round(input_cost, 6),
            "output_usd": round(output_cost, 6),
            "cache_write_usd": round(cache_write_cost, 6),
            "cache_read_usd": round(cache_read_cost, 6),
        },
        "pricing_basis": "anthropic_official_api_pricing",
    }


def build_analysis_usage_payload(usage: Any, model: str | None, *, duration_seconds: float | None = None) -> dict[str, Any]:
    input_tokens = _as_int(getattr(usage, "input_tokens", None))
    output_tokens = _as_int(getattr(usage, "output_tokens", None))
    cache_creation_input_tokens = _as_int(getattr(usage, "cache_creation_input_tokens", None))
    cache_read_input_tokens = _as_int(getattr(usage, "cache_read_input_tokens", None))
    total_tokens = sum(
        value or 0
        for value in [input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens]
    )
    cost_payload = estimate_ai_cost_usd(
        model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=cache_creation_input_tokens,
        cache_read_input_tokens=cache_read_input_tokens,
    ) or {}
    return {
        "provider": "anthropic",
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_input_tokens": cache_creation_input_tokens,
        "cache_read_input_tokens": cache_read_input_tokens,
        "total_tokens": total_tokens or None,
        "duration_seconds": duration_seconds,
        "credits": None,
        "credits_note": "Přesné kredity nejsou z API dostupné.",
        **cost_payload,
    }


def merge_analysis_usage_payloads(payloads: list[dict[str, Any]], model: str | None) -> dict[str, Any] | None:
    clean_payloads = [payload for payload in payloads if payload]
    if not clean_payloads:
        return None

    numeric_keys = [
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "total_tokens",
        "duration_seconds",
    ]
    merged: dict[str, Any] = {
        "provider": "anthropic",
        "model": model,
        "request_count": len(clean_payloads),
        "repair_request_used": len(clean_payloads) > 1,
        "credits": None,
        "credits_note": "Přesné kredity nejsou z API dostupné.",
    }
    for key in numeric_keys:
        values = [payload.get(key) for payload in clean_payloads if payload.get(key) is not None]
        if not values:
            merged[key] = None
        elif key == "duration_seconds":
            merged[key] = round(sum(values), 2)
        else:
            merged[key] = sum(values)

    cost_payload = estimate_ai_cost_usd(
        model,
        input_tokens=merged.get("input_tokens"),
        output_tokens=merged.get("output_tokens"),
        cache_creation_input_tokens=merged.get("cache_creation_input_tokens"),
        cache_read_input_tokens=merged.get("cache_read_input_tokens"),
    )
    if cost_payload:
        merged.update(cost_payload)
    return merged


def repair_ai_json(raw_text: str, client: Anthropic) -> dict[str, Any]:
    repair_prompt = f"""
Převeď následující odpověď do validního JSON.

Pravidla:
- vrať pouze validní JSON objekt
- zachovej význam původního textu
- nepřidávej žádné nové informace
- oprav jen syntaxi a strukturu
- použij přesně tento tvar:
{{
  "analysis_overview": "string",
  "data_quality_note": "string",
  "insight_summary": [{{"title": "string", "detail": "string"}}],
  "deep_insights": [{{"title": "string", "detail": "string"}}],
  "praskac": [{{"title": "string", "detail": "string"}}]
}}

Původní odpověď:
{raw_text}
"""
    t0 = time.time()
    response = client.messages.create(
        model=AI_MODEL,
        max_tokens=2600,
        temperature=0,
        messages=[{"role": "user", "content": repair_prompt}],
    )
    duration = round(time.time() - t0, 2)
    usage = getattr(response, "usage", None)
    logger.info(
        "repair_ai_json model=%s input_tokens=%s output_tokens=%s duration_s=%s",
        AI_MODEL,
        getattr(usage, "input_tokens", None),
        getattr(usage, "output_tokens", None),
        duration,
    )
    repaired_text = "\n".join(block.text for block in response.content if getattr(block, "type", None) == "text")
    return {
        "parsed": extract_json_block(repaired_text),
        "usage": build_analysis_usage_payload(usage, AI_MODEL, duration_seconds=duration),
    }


def compact_people_for_ai(items: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for item in items[:limit]:
        compact.append({
            "role": item.get("role"),
            "name": item.get("name"),
            "raw": item.get("raw"),
        })
    return compact


def compact_docs_for_ai(docs: list[dict[str, Any]], limit: int = 4) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for doc in docs[:limit]:
        compact.append({
            "document_number": doc.get("document_number"),
            "type": doc.get("type"),
            "years": doc.get("years"),
            "received_date": doc.get("received_date"),
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
                for item in (doc.get("candidate_files") or [])[:2]
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


def fallback_ai_analysis(
    overview_fallback: list[dict[str, str]],
    deep_fallback: list[dict[str, str]],
    praskac_fallback: list[dict[str, str]],
    *,
    engine: str,
) -> dict[str, Any]:
    overview_text = (
        "AI vrstva je vypnutá. Níže je pravidlový výstup z veřejných podkladů justice.cz."
        if engine == "disabled"
        else "Shrnutí běží bez AI vrstvy. Níže je pravidlový výstup z veřejných podkladů justice.cz."
    )
    return {
        "analysis_engine": engine,
        "analysis_model": None,
        "analysis_usage": None,
        "analysis_overview": overview_text,
        "data_quality_note": "Kvalita dat závisí na čitelnosti veřejných PDF a úplnosti Sbírky listin.",
        "insight_summary": overview_fallback,
        "deep_insights": deep_fallback,
        "praskac": praskac_fallback,
    }


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
Jsi analytik \u010desk\u00fdch firem. Pracuj jen s dodan\u00fdmi ve\u0159ejn\u00fdmi podklady z justice.cz a Sb\u00edrky listin.

Pravidla:
- Nep\u0159id\u00e1vej nic, co nen\u00ed podlo\u017eeno daty v payloadu.
- Kdy\u017e jsou data slab\u00e1 nebo ne\u00fapln\u00e1, \u0159ekni to p\u0159\u00edmo.
- Pi\u0161 \u010desky, stru\u010dn\u011b, v\u011bcn\u011b a prakticky.
- Sekce Pr\u00e1ska\u010d m\u00e1 b\u00fdt p\u0159\u00edmo\u010dar\u00e1, ale po\u0159\u00e1d faktick\u00e1 a bez nepodlo\u017een\u00fdch obvin\u011bn\u00ed.
- Hledej trendy v r\u016fstu, poklesu, ziskovosti, zadlu\u017een\u00ed, kapit\u00e1lu, chyb\u011bj\u00edc\u00edch letech a zm\u011bn\u00e1ch ve veden\u00ed.
- Pokud nejsou jasn\u00e9 finan\u010dn\u00ed z\u00e1v\u011bry, p\u0159iznej omezen\u00ed m\u00edsto spekulace.

Vra\u0165 pouze JSON v tomto tvaru:
{{
  "analysis_overview": "max 3 kr\u00e1tk\u00e9 v\u011bty shrnut\u00ed",
  "data_quality_note": "jedna v\u011bta o kvalit\u011b a limitech dat",
  "insight_summary": [{{"title": "...", "detail": "..."}}],
  "deep_insights": [{{"title": "...", "detail": "..."}}],
  "praskac": [{{"title": "...", "detail": "..."}}]
}}

Dal\u0161\u00ed limity:
- insight_summary max 4 polo\u017eky
- deep_insights max 5 polo\u017eek
- praskac max 5 polo\u017eek
- title i detail dr\u017e kr\u00e1tk\u00e9 a konkr\u00e9tn\u00ed

Payload:
{json.dumps(payload, ensure_ascii=False, indent=2)}
"""
    client = Anthropic(api_key=ANTHROPIC_API_KEY, timeout=AI_TIMEOUT_SECONDS)
    t0 = time.time()
    try:
        response = client.messages.create(
            model=AI_MODEL,
            max_tokens=2600,
            temperature=0.2,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception:
        logger.exception(f"generate_ai_analysis error model={AI_MODEL}")
        raise
    duration = round(time.time() - t0, 2)
    usage = getattr(response, "usage", None)
    input_tokens = getattr(usage, "input_tokens", None)
    output_tokens = getattr(usage, "output_tokens", None)
    logger.info(f"generate_ai_analysis model={AI_MODEL} input_tokens={input_tokens} output_tokens={output_tokens} duration_s={duration}")
    usage_payloads = [build_analysis_usage_payload(usage, AI_MODEL, duration_seconds=duration)]
    text = "\n".join(block.text for block in response.content if getattr(block, "type", None) == "text")
    try:
        parsed = extract_json_block(text)
    except Exception:
        logger.warning("generate_ai_analysis malformed_json attempting_repair")
        repair_result = repair_ai_json(text, client)
        parsed = repair_result["parsed"]
        usage_payloads.append(repair_result["usage"])
    return {
        "analysis_engine": "ai",
        "analysis_model": AI_MODEL,
        "analysis_usage": merge_analysis_usage_payloads(usage_payloads, AI_MODEL),
        "analysis_overview": norm_text(str(parsed.get("analysis_overview") or "")) or "AI rozbor z ve\u0159ejn\u00fdch listin nen\u00ed k dispozici.",
        "data_quality_note": norm_text(str(parsed.get("data_quality_note") or "")) or "Kvalita dat z\u00e1vis\u00ed na \u010ditelnosti ve\u0159ejn\u00fdch PDF a \u00faplnosti Sb\u00edrky listin.",
        "insight_summary": clean_ai_items(parsed.get("insight_summary"), overview_fallback, 6),
        "deep_insights": clean_ai_items(parsed.get("deep_insights"), deep_fallback, 8),
        "praskac": clean_ai_items(parsed.get("praskac"), praskac_fallback, 8),
    }


def resolve_ai_analysis(
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
    if AI_ENABLED:
        try:
            return generate_ai_analysis(
                company_name=company_name,
                ico=ico,
                basic_info_items=basic_info_items,
                executives=executives,
                owners=owners,
                history=history,
                timeline=timeline,
                docs=docs,
                overview_fallback=overview_fallback,
                deep_fallback=deep_fallback,
                praskac_fallback=praskac_fallback,
            )
        except Exception:
            logger.exception("generate_ai_analysis failed, using fallback")
            return fallback_ai_analysis(
                overview_fallback,
                deep_fallback,
                praskac_fallback,
                engine="fallback",
            )
    return fallback_ai_analysis(
        overview_fallback,
        deep_fallback,
        praskac_fallback,
        engine="disabled",
    )


def build_basic_info(current_extract: dict[str, Any]) -> list[dict[str, str]]:
    info = current_extract.get("basic_info", {})
    ordered_keys = [
        "Obchodn\u00ed firma",
        "Identifika\u010dn\u00ed \u010d\u00edslo",
        "Pr\u00e1vn\u00ed forma",
        "Datum vzniku a z\u00e1pisu",
        "Spisov\u00e1 zna\u010dka",
        "S\u00eddlo",
    ]
    items = []
    for key in ordered_keys:
        if info.get(key):
            items.append({"label": key, "value": info[key]})
    for extra_key in ["P\u0159edm\u011bt podnik\u00e1n\u00ed", "Z\u00e1kladn\u00ed kapit\u00e1l"]:
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
        pattern = re.compile(rf">\s*{re.escape(label)}\s*<.*?>\s*<span[^>]*>\s*([0-9\s]+)\s*K\u010d", re.I | re.S)
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
    emp_match = re.search(r">\s*Po\u010det zam\u011bstnanc\u016f\s*<.*?>\s*<span[^>]*>\s*([^<]+)<", html, re.I | re.S)
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
                "label": "Aktiva vs. Chytr\u00fd rejst\u0159\u00edk",
                "status": "ok" if abs(diff) <= 2 else "warning",
                "app_value": assets,
                "external_value": snapshot["assets_mil_czk"],
                "detail": "Rozd\u00edl do 2 mil. K\u010d beru jako p\u0159ijateln\u00fd kv\u016fli zaokrouhlen\u00ed." if abs(diff) <= 2 else "Hodnoty se rozch\u00e1zej\u00ed, chce to ru\u010dn\u00ed kontrolu PDF.",
            }
        )
    profit = latest.get("net_profit")
    if profit is not None and snapshot.get("profit_mil_czk") is not None:
        diff = round(profit - snapshot["profit_mil_czk"], 2)
        checks.append(
            {
                "label": "Zisk vs. Chytr\u00fd rejst\u0159\u00edk",
                "status": "ok" if abs(diff) <= 2 else "warning",
                "app_value": profit,
                "external_value": snapshot["profit_mil_czk"],
                "detail": "Rozd\u00edl do 2 mil. K\u010d beru jako p\u0159ijateln\u00fd kv\u016fli zaokrouhlen\u00ed." if abs(diff) <= 2 else "Hodnoty se rozch\u00e1zej\u00ed, chce to ru\u010dn\u00ed kontrolu PDF.",
            }
        )
    return {
        "source_name": "Chytr\u00fd rejst\u0159\u00edk",
        "source_url": snapshot["url"],
        "employees_hint": snapshot.get("employees_hint"),
        "checks": checks,
        "snapshot": snapshot,
    }


def enhance_company_profile_with_ai(profile: dict[str, Any], visitor_id: str | None = None, query: str | None = None) -> dict[str, Any]:
    ai_analysis = resolve_ai_analysis(
        company_name=str(profile.get("name") or "Společnost"),
        ico=clean_ico(str(profile.get("ico") or "")),
        basic_info_items=list(profile.get("basic_info") or []),
        executives=list(profile.get("executives") or []),
        owners=list(profile.get("owners") or []),
        history=dict(profile.get("history_signals") or {}),
        timeline=list(profile.get("financial_timeline") or []),
        docs=list(profile.get("financial_documents") or []),
        overview_fallback=list(profile.get("insight_summary") or []),
        deep_fallback=list(profile.get("deep_insights") or []),
        praskac_fallback=list(profile.get("praskac") or []),
    )
    updated = dict(profile)
    updated.update(
        {
            "analysis_engine": ai_analysis["analysis_engine"],
            "analysis_model": ai_analysis.get("analysis_model"),
            "analysis_usage": ai_analysis.get("analysis_usage"),
            "analysis_overview": ai_analysis["analysis_overview"],
            "data_quality_note": ai_analysis["data_quality_note"],
            "insight_summary": ai_analysis["insight_summary"],
            "deep_insights": ai_analysis["deep_insights"],
            "praskac": ai_analysis["praskac"],
            "generated_at": datetime.now().astimezone().isoformat(),
            "cache_status": "fresh",
        }
    )
    save_history_entry(visitor_id, updated, query=query)
    return updated
