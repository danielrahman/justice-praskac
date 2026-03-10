"""Tests for justice.ai profile building helpers."""
from __future__ import annotations

import pytest

from justice.ai import (
    build_analysis_usage_payload,
    build_highlights,
    estimate_ai_cost_usd,
    extract_history_events,
    extract_json_block,
    merge_analysis_usage_payloads,
)


# ---------------------------------------------------------------------------
# build_highlights
# ---------------------------------------------------------------------------
class TestBuildHighlights:
    def _make_timeline(self):
        """Create a sample timeline with financial data for two years."""
        return [
            {
                "year": 2022,
                "revenue": 100.0,
                "operating_profit": 15.0,
                "net_profit": 10.0,
                "assets": 200.0,
                "equity": 80.0,
                "liabilities": 120.0,
                "debt": 30.0,
                "net_margin_pct": 10.0,
                "equity_ratio_pct": 40.0,
                "liability_ratio_pct": 60.0,
                "debt_to_revenue_pct": 30.0,
            },
            {
                "year": 2023,
                "revenue": 120.0,
                "operating_profit": 18.0,
                "net_profit": 12.0,
                "assets": 220.0,
                "equity": 90.0,
                "liabilities": 130.0,
                "debt": 35.0,
                "net_margin_pct": 10.0,
                "equity_ratio_pct": 40.9,
                "liability_ratio_pct": 59.1,
                "debt_to_revenue_pct": 29.2,
            },
        ]

    def test_returns_three_lists(self):
        timeline = self._make_timeline()
        overview, deep, praskac = build_highlights(timeline, [], {})
        assert isinstance(overview, list)
        assert isinstance(deep, list)
        assert isinstance(praskac, list)

    def test_overview_not_empty(self):
        timeline = self._make_timeline()
        overview, _, _ = build_highlights(timeline, [], {})
        assert len(overview) >= 1

    def test_deep_not_empty(self):
        timeline = self._make_timeline()
        _, deep, _ = build_highlights(timeline, [], {})
        assert len(deep) >= 1

    def test_praskac_not_empty(self):
        timeline = self._make_timeline()
        _, _, praskac = build_highlights(timeline, [], {})
        assert len(praskac) >= 1

    def test_each_item_has_title_and_detail(self):
        timeline = self._make_timeline()
        overview, deep, praskac = build_highlights(timeline, [], {})
        for items in (overview, deep, praskac):
            for item in items:
                assert "title" in item
                assert "detail" in item
                assert isinstance(item["title"], str)
                assert isinstance(item["detail"], str)

    def test_empty_timeline_still_returns_items(self):
        overview, deep, praskac = build_highlights([], [], {})
        # Should still produce fallback items
        assert len(overview) >= 1
        assert len(deep) >= 1
        assert len(praskac) >= 1

    def test_negative_profit_triggers_praskac(self):
        timeline = [
            {
                "year": 2022,
                "revenue": 100.0,
                "net_profit": -5.0,
                "assets": 200.0,
                "equity": 80.0,
                "liabilities": 120.0,
                "net_margin_pct": -5.0,
                "equity_ratio_pct": 40.0,
                "liability_ratio_pct": 60.0,
            },
            {
                "year": 2023,
                "revenue": 90.0,
                "net_profit": -8.0,
                "assets": 180.0,
                "equity": 70.0,
                "liabilities": 110.0,
                "net_margin_pct": -8.9,
                "equity_ratio_pct": 38.9,
                "liability_ratio_pct": 61.1,
            },
        ]
        _, _, praskac = build_highlights(timeline, [], {})
        praskac_titles = [item["title"] for item in praskac]
        # Should mention the company is in loss or has repeated losses
        has_loss_mention = any("ztrát" in title.lower() for title in praskac_titles)
        assert has_loss_mention, f"Expected loss-related praskac, got: {praskac_titles}"

    def test_high_liability_ratio_triggers_praskac(self):
        timeline = [
            {
                "year": 2023,
                "revenue": 100.0,
                "net_profit": 5.0,
                "assets": 200.0,
                "equity": 30.0,
                "liabilities": 170.0,
                "net_margin_pct": 5.0,
                "equity_ratio_pct": 15.0,
                "liability_ratio_pct": 85.0,
            },
        ]
        _, _, praskac = build_highlights(timeline, [], {})
        praskac_titles = [item["title"] for item in praskac]
        has_liability_mention = any("cizích zdrojích" in title.lower() or "závislost" in title.lower() for title in praskac_titles)
        assert has_liability_mention, f"Expected liability-related praskac, got: {praskac_titles}"

    def test_late_filed_documents_are_not_reported(self):
        docs = [{"years": [2023], "filed_date": "2025-12-31"}]
        _, deep, praskac = build_highlights([], docs, {})
        all_titles = [item["title"].lower() for item in [*deep, *praskac]]
        assert not any("založení podkladů" in title for title in all_titles), all_titles


# ---------------------------------------------------------------------------
# extract_history_events
# ---------------------------------------------------------------------------
class TestExtractHistoryEvents:
    def test_basic_structure(self):
        extract = {"rows": []}
        result = extract_history_events(extract)
        assert "name_changes" in result
        assert "address_changes" in result
        assert "management_turnover" in result

    def test_counts_name_change(self):
        extract = {
            "rows": [
                {"label": "Obchodní firma", "value": "Foo s.r.o.", "history": "Vymazáno: Bar s.r.o."},
            ]
        }
        result = extract_history_events(extract)
        assert result["name_changes"] == 1

    def test_counts_address_change(self):
        extract = {
            "rows": [
                {"label": "Sídlo", "value": "Praha 1", "history": "Vymazáno: Brno"},
            ]
        }
        result = extract_history_events(extract)
        assert result["address_changes"] == 1

    def test_counts_management_turnover(self):
        extract = {
            "rows": [
                {"label": "Jednatel", "value": "Jan Novák", "history": "Vymazáno: Petr Novák"},
                {"label": "Člen představenstva", "value": "Marie Nová", "history": "Vymazáno: Eva Stará"},
            ]
        }
        result = extract_history_events(extract)
        assert result["management_turnover"] == 2

    def test_counts_management_turnover_for_unlabeled_rows_after_role_header(self):
        extract = {
            "rows": [
                {"label": "Statutární orgán", "value": "", "history": ""},
                {"label": "Jednatel", "value": "", "history": ""},
                {"label": "", "value": "HIEU MINH VU Den vzniku funkce: 5. října 2022", "history": "zapsáno 5. října 2022 vymazáno 12. dubna 2024"},
                {"label": "", "value": "HIEU MINH VU Den vzniku funkce: 5. října 2022 Den zániku funkce: 18. února 2026", "history": "zapsáno 12. dubna 2024 vymazáno 18. února 2026"},
                {"label": "Počet členů", "value": "1", "history": ""},
            ]
        }
        result = extract_history_events(extract)
        assert result["management_turnover"] == 2

    def test_no_changes(self):
        extract = {
            "rows": [
                {"label": "Obchodní firma", "value": "Foo s.r.o.", "history": ""},
                {"label": "Sídlo", "value": "Praha 1", "history": ""},
            ]
        }
        result = extract_history_events(extract)
        assert result["name_changes"] == 0
        assert result["address_changes"] == 0
        assert result["management_turnover"] == 0

    def test_empty_rows(self):
        result = extract_history_events({"rows": []})
        assert result["name_changes"] == 0
        assert result["address_changes"] == 0
        assert result["management_turnover"] == 0

    def test_missing_rows_key(self):
        result = extract_history_events({})
        assert result["name_changes"] == 0
        assert result["address_changes"] == 0
        assert result["management_turnover"] == 0


# ---------------------------------------------------------------------------
# extract_json_block
# ---------------------------------------------------------------------------
class TestExtractJsonBlock:
    def test_parses_fenced_json(self):
        raw = """```json
        {"analysis_overview":"ok","data_quality_note":"ok","insight_summary":[],"deep_insights":[],"praskac":[]}
        ```"""
        parsed = extract_json_block(raw)
        assert parsed["analysis_overview"] == "ok"

    def test_parses_json_wrapped_in_text(self):
        raw = 'text before {"analysis_overview":"ok","data_quality_note":"ok","insight_summary":[],"deep_insights":[],"praskac":[]} text after'
        parsed = extract_json_block(raw)
        assert parsed["data_quality_note"] == "ok"

    def test_recovers_trailing_commas(self):
        raw = """{
          "analysis_overview": "ok",
          "data_quality_note": "ok",
          "insight_summary": [],
          "deep_insights": [],
          "praskac": [],
        }"""
        parsed = extract_json_block(raw)
        assert parsed["praskac"] == []


class TestAiUsageAccounting:
    def test_estimates_opus_46_cost(self):
        cost = estimate_ai_cost_usd(
            "claude-opus-4-6",
            input_tokens=4_000,
            output_tokens=2_000,
        )

        assert cost is not None
        assert cost["pricing_model"] == "claude-opus-4.6"
        assert cost["estimated_cost_usd"] == pytest.approx(0.07, rel=0.01)

    def test_build_usage_payload_includes_totals_and_cost(self):
        usage = type(
            "Usage",
            (),
            {
                "input_tokens": 3_500,
                "output_tokens": 1_200,
                "cache_creation_input_tokens": None,
                "cache_read_input_tokens": None,
            },
        )()

        payload = build_analysis_usage_payload(usage, "claude-sonnet-4-20250514", duration_seconds=12.5)

        assert payload["total_tokens"] == 4_700
        assert payload["duration_seconds"] == 12.5
        assert payload["estimated_cost_usd"] == pytest.approx(0.0285, rel=0.01)

    def test_merge_usage_payloads_sums_repair_request(self):
        first = {
            "input_tokens": 1_000,
            "output_tokens": 500,
            "cache_creation_input_tokens": None,
            "cache_read_input_tokens": None,
            "total_tokens": 1_500,
            "duration_seconds": 10.0,
        }
        second = {
            "input_tokens": 800,
            "output_tokens": 200,
            "cache_creation_input_tokens": None,
            "cache_read_input_tokens": None,
            "total_tokens": 1_000,
            "duration_seconds": 4.0,
        }

        payload = merge_analysis_usage_payloads([first, second], "claude-opus-4-6")

        assert payload is not None
        assert payload["request_count"] == 2
        assert payload["repair_request_used"] is True
        assert payload["input_tokens"] == 1_800
        assert payload["output_tokens"] == 700
        assert payload["total_tokens"] == 2_500
        assert payload["duration_seconds"] == 14.0
