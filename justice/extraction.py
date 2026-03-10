from __future__ import annotations

import re
from typing import Any

from justice.db import upsert_document
from justice.documents import (
    build_metric_source_text,
    detect_unit_multiplier,
    get_pdf_text,
    parse_document_detail,
)
from justice.storage_r2 import upload_document_pdf, upload_document_text
from justice.utils import (
    METRIC_PATTERNS,
    combine_digit_groups,
    is_probable_year,
    logger,
    looks_like_year_header,
    norm_key,
    norm_text,
    parse_adjacent_metric,
    parse_line_two_values,
    parse_loose_number,
    parse_number_candidates,
    public_error_message,
    split_digit_groups,
    trim_leading_label_groups,
)


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


def _persist_document_artifacts(
    *,
    doc_copy: dict[str, Any],
    attachment_copy: dict[str, Any],
    pdf_text: dict[str, Any],
    metric_text: str,
    found_metrics: list[str],
    company_name: str,
    ico: str,
) -> None:
    subject_id = str(doc_copy.get("subjekt_id") or "")
    detail_url = str(doc_copy.get("detail_url") or "")
    pdf_bytes = pdf_text.get("pdf_bytes") or b""
    content_sha256 = str(pdf_text.get("content_sha256") or "")
    if not subject_id or not detail_url or not pdf_bytes or not content_sha256:
        return
    pdf_key = upload_document_pdf(subject_id, content_sha256, pdf_bytes)
    attachment_copy["content_sha256"] = content_sha256
    attachment_copy["storage_key"] = pdf_key

    text_key = None
    text_kind = None
    selected_text = str(pdf_text.get("text") or "").strip()
    if selected_text and (str(pdf_text.get("mode") or "") == "ocr" or bool(found_metrics)):
        text_key = upload_document_text(subject_id, content_sha256, selected_text)
        text_kind = "ocr" if str(pdf_text.get("mode") or "") == "ocr" else "selected_extract"
        attachment_copy["text_storage_key"] = text_key

    upsert_document(
        {
            "subject_id": subject_id,
            "ico": ico,
            "company_name": company_name,
            "detail_url": detail_url,
            "pdf_index": attachment_copy.get("pdf_index") or 0,
            "content_sha256": content_sha256,
            "source_url": attachment_copy.get("url"),
            "r2_pdf_key": pdf_key,
            "r2_text_key": text_key,
            "text_kind": text_kind,
            "document_id": doc_copy.get("document_id"),
            "spis": doc_copy.get("spis"),
            "document_number": doc_copy.get("document_number"),
            "doc_type": doc_copy.get("type"),
            "primary_year": (doc_copy.get("years") or [None])[0],
            "created_date": doc_copy.get("created_date"),
            "received_date": doc_copy.get("received_date"),
            "filed_date": doc_copy.get("filed_date"),
            "page_count": attachment_copy.get("page_count") or pdf_text.get("page_count") or 0,
            "extraction_mode": attachment_copy.get("extraction_mode"),
            "metrics_found": found_metrics,
            "used_in_profile": True,
        }
    )


def extract_financial_doc_data(
    doc: dict[str, Any],
    *,
    company_name: str = "",
    ico: str = "",
) -> tuple[dict[str, Any], dict[int, dict[str, float]]]:
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
            _persist_document_artifacts(
                doc_copy=doc_copy,
                attachment_copy=attachment_copy,
                pdf_text=pdf_text,
                metric_text=metric_text,
                found_metrics=found_metrics,
                company_name=company_name,
                ico=ico,
            )
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
                    _persist_document_artifacts(
                        doc_copy=doc_copy,
                        attachment_copy=attachment_copy,
                        pdf_text=pdf_text,
                        metric_text=metric_text,
                        found_metrics=found_metrics,
                        company_name=company_name,
                        ico=ico,
                    )
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
