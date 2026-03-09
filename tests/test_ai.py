"""Tests for justice.ai profile building helpers."""
from __future__ import annotations

import pytest

from justice.ai import build_highlights, extract_history_events


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
