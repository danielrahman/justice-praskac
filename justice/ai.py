from __future__ import annotations

import json
import re
import time
from datetime import datetime
from typing import Any

from anthropic import Anthropic

from justice.db import save_history_entry
from justice.documents import parse_document_list, pick_recent_financial_docs
from justice.extraction import (
    extract_financial_doc_data,
    finalize_financial_timeline,
    merge_doc_year_map,
    merge_financial_timeline,
    pct_change,
    summarize_timeline,
)
from justice.scraping import (
    SESSION,
    clean_ico,
    fetch_extract,
)
from justice.utils import (
    AI_ENABLED,
    AI_MODEL,
    AI_TIMEOUT_SECONDS,
    BASE_UI,
    PROFILE_CACHE_TTL_SECONDS,
    PROFILE_CACHE_VERSION,
    days_between,
    load_json_cache,
    logger,
    norm_key,
    norm_text,
    save_json_cache,
    strip_accents,
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

    for doc in docs:
        primary_year = (doc.get("years") or [None])[0]
        if not primary_year:
            continue
        year_end = f"{primary_year}-12-31"
        delay = days_between(year_end, doc.get("filed_date"))
        if delay and delay > 365:
            praskac.append({
                "title": f"Pozdn\u00ed zalo\u017een\u00ed podklad\u016f za {primary_year}",
                "detail": f"Dokument byl do Sb\u00edrky listin zalo\u017een p\u0159ibli\u017en\u011b {delay} dn\u00ed po konci roku.",
            })
        elif delay and delay > 240:
            deep.append({
                "title": f"Pomale\u0161\u0161\u00ed zalo\u017een\u00ed podklad\u016f za {primary_year}",
                "detail": f"Dokument byl do Sb\u00edrky listin zalo\u017een asi {delay} dn\u00ed po konci roku.",
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
Jsi analytik \u010desk\u00fdch firem. Pracuj jen s dodan\u00fdmi ve\u0159ejn\u00fdmi podklady z justice.cz a Sb\u00edrky listin.

Pravidla:
- Nep\u0159id\u00e1vej nic, co nen\u00ed podlo\u017eeno daty v payloadu.
- Kdy\u017e jsou data slab\u00e1 nebo ne\u00fapln\u00e1, \u0159ekni to p\u0159\u00edmo.
- Pi\u0161 \u010desky, stru\u010dn\u011b, v\u011bcn\u011b a prakticky.
- Sekce Pr\u00e1ska\u010d m\u00e1 b\u00fdt p\u0159\u00edmo\u010dar\u00e1, ale po\u0159\u00e1d faktick\u00e1 a bez nepodlo\u017een\u00fdch obvin\u011bn\u00ed.
- Hledej trendy v r\u016fstu, poklesu, ziskovosti, zadlu\u017een\u00ed, kapit\u00e1lu, chyb\u011bj\u00edc\u00edch letech, pozdn\u00edch listin\u00e1ch a zm\u011bn\u00e1ch ve veden\u00ed.
- Pokud nejsou jasn\u00e9 finan\u010dn\u00ed z\u00e1v\u011bry, p\u0159iznej omezen\u00ed m\u00edsto spekulace.

Vra\u0165 pouze JSON v tomto tvaru:
{{
  "analysis_overview": "2-4 v\u011bty shrnut\u00ed v jedn\u00e9 kr\u00e1tk\u00e9 odstavcov\u00e9 pas\u00e1\u017ei",
  "data_quality_note": "jedna v\u011bta o kvalit\u011b a limitech dat",
  "insight_summary": [{{"title": "...", "detail": "..."}}],
  "deep_insights": [{{"title": "...", "detail": "..."}}],
  "praskac": [{{"title": "...", "detail": "..."}}]
}}

Payload:
{json.dumps(payload, ensure_ascii=False, indent=2)}
"""
    client = Anthropic(timeout=AI_TIMEOUT_SECONDS)
    t0 = time.time()
    try:
        response = client.messages.create(
            model=AI_MODEL,
            max_tokens=1800,
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
    usage_payload = {
        "provider": "anthropic",
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", None),
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", None),
        "credits": None,
        "credits_note": "P\u0159esn\u00e9 kredity nejsou z API dostupn\u00e9.",
    }
    text = "\n".join(block.text for block in response.content if getattr(block, "type", None) == "text")
    parsed = extract_json_block(text)
    return {
        "analysis_engine": "ai",
        "analysis_model": AI_MODEL,
        "analysis_usage": usage_payload,
        "analysis_overview": norm_text(str(parsed.get("analysis_overview") or "")) or "AI rozbor z ve\u0159ejn\u00fdch listin nen\u00ed k dispozici.",
        "data_quality_note": norm_text(str(parsed.get("data_quality_note") or "")) or "Kvalita dat z\u00e1vis\u00ed na \u010ditelnosti ve\u0159ejn\u00fdch PDF a \u00faplnosti Sb\u00edrky listin.",
        "insight_summary": clean_ai_items(parsed.get("insight_summary"), overview_fallback, 6),
        "deep_insights": clean_ai_items(parsed.get("deep_insights"), deep_fallback, 8),
        "praskac": clean_ai_items(parsed.get("praskac"), praskac_fallback, 8),
    }


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
    company_name = current_extract.get("basic_info", {}).get("Obchodn\u00ed firma") or current_extract.get("subtitle") or "Spole\u010dnost"
    ico = clean_ico(str(current_extract.get("basic_info", {}).get("Identifika\u010dn\u00ed \u010d\u00edslo", "")))

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
