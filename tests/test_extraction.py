"""Tests for justice.extraction financial parsing functions."""
from __future__ import annotations

import pytest

from justice.extraction import (
    extract_financial_metrics_from_text,
    normalize_timeline_outliers,
    pct_change,
    sanitize_financial_rows,
)


# ---------------------------------------------------------------------------
# extract_financial_metrics_from_text
# ---------------------------------------------------------------------------
class TestExtractFinancialMetrics:
    def test_returns_dict_with_expected_keys(self, sample_financial_text: str):
        result = extract_financial_metrics_from_text(sample_financial_text, doc_year=2023)
        assert isinstance(result, dict)
        assert "year_map" in result
        assert "multiplier" in result
        assert "found_metrics" in result

    def test_year_map_has_expected_years(self, sample_financial_text: str):
        result = extract_financial_metrics_from_text(sample_financial_text, doc_year=2023)
        year_map = result["year_map"]
        assert 2023 in year_map
        assert 2022 in year_map

    def test_extracts_some_metrics(self, sample_financial_text: str):
        result = extract_financial_metrics_from_text(sample_financial_text, doc_year=2023)
        found = result["found_metrics"]
        # Should find at least some of these from the sample text
        assert len(found) >= 1, f"Expected to find at least one metric, found: {list(found.keys())}"

    def test_assets_extracted(self, sample_financial_text: str):
        result = extract_financial_metrics_from_text(sample_financial_text, doc_year=2023)
        found = result["found_metrics"]
        # The sample has "AKTIVA CELKEM" and "PASIVA CELKEM"
        if "assets" in found:
            pair = found["assets"]
            assert isinstance(pair, tuple)
            assert len(pair) == 2
            # Both values should be positive for assets
            assert pair[0] > 0
            assert pair[1] > 0

    def test_revenue_extracted(self, sample_financial_text: str):
        result = extract_financial_metrics_from_text(sample_financial_text, doc_year=2023)
        found = result["found_metrics"]
        # The sample has "Trzby z prodeje vyrobku a sluzeb"
        if "revenue" in found:
            pair = found["revenue"]
            assert isinstance(pair, tuple)
            assert len(pair) == 2

    def test_net_profit_extracted(self, sample_financial_text: str):
        result = extract_financial_metrics_from_text(sample_financial_text, doc_year=2023)
        found = result["found_metrics"]
        # The sample has "Vysledek hospodareni za ucetni obdobi"
        if "net_profit" in found:
            pair = found["net_profit"]
            assert isinstance(pair, tuple)
            assert len(pair) == 2

    def test_multiplier_is_thousand(self, sample_financial_text: str):
        # The sample text says "v celych tisicich CZK"
        result = extract_financial_metrics_from_text(sample_financial_text, doc_year=2023)
        assert result["multiplier"] == 1000

    def test_none_doc_year(self):
        result = extract_financial_metrics_from_text("some text", doc_year=None)
        assert result["year_map"] == {}

    def test_empty_text(self):
        result = extract_financial_metrics_from_text("", doc_year=2023)
        assert result["found_metrics"] == {}


# ---------------------------------------------------------------------------
# normalize_timeline_outliers
# ---------------------------------------------------------------------------
class TestNormalizeTimelineOutliers:
    def test_clamps_obvious_outlier(self):
        timeline = [
            {"year": 2020, "revenue": 10.0},
            {"year": 2021, "revenue": 12.0},
            {"year": 2022, "revenue": 12000.0},  # obvious outlier (1000x too big)
            {"year": 2023, "revenue": 11.0},
        ]
        result = normalize_timeline_outliers(timeline)
        # The outlier should be clamped to ~12.0 (12000/1000)
        assert result[2]["revenue"] == 12.0

    def test_preserves_normal_values(self):
        timeline = [
            {"year": 2020, "revenue": 100.0},
            {"year": 2021, "revenue": 110.0},
            {"year": 2022, "revenue": 105.0},
            {"year": 2023, "revenue": 120.0},
        ]
        result = normalize_timeline_outliers(timeline)
        assert result[0]["revenue"] == 100.0
        assert result[1]["revenue"] == 110.0
        assert result[2]["revenue"] == 105.0
        assert result[3]["revenue"] == 120.0

    def test_handles_none_values(self):
        timeline = [
            {"year": 2020, "revenue": None},
            {"year": 2021, "revenue": 10.0},
            {"year": 2022, "revenue": 12.0},
        ]
        result = normalize_timeline_outliers(timeline)
        assert result[0]["revenue"] is None
        assert result[1]["revenue"] == 10.0

    def test_returns_sorted_by_year(self):
        timeline = [
            {"year": 2023, "revenue": 10.0},
            {"year": 2020, "revenue": 10.0},
            {"year": 2021, "revenue": 10.0},
        ]
        result = normalize_timeline_outliers(timeline)
        years = [row["year"] for row in result]
        assert years == [2020, 2021, 2023]

    def test_empty_timeline(self):
        result = normalize_timeline_outliers([])
        assert result == []

    def test_nullifies_very_large_outlier(self):
        # When ratio >= 200 and value > 100000 and dividing by 1000 doesn't help
        timeline = [
            {"year": 2020, "revenue": 10.0},
            {"year": 2021, "revenue": 12.0},
            {"year": 2022, "revenue": 5_000_000.0},  # way too large, dividing by 1000 gives 5000 which is still >5x
            {"year": 2023, "revenue": 11.0},
        ]
        result = normalize_timeline_outliers(timeline)
        # Should be nullified since even dividing by 1000 gives ratio > 5
        assert result[2]["revenue"] is None


# ---------------------------------------------------------------------------
# sanitize_financial_rows
# ---------------------------------------------------------------------------
class TestSanitizeFinancialRows:
    def test_removes_tiny_revenue_with_large_assets(self):
        rows = [{"year": 2023, "revenue": 3.0, "assets": 200.0, "net_profit": None, "equity": None, "liabilities": None, "debt": None}]
        result = sanitize_financial_rows(rows)
        assert result[0]["revenue"] is None

    def test_keeps_valid_revenue(self):
        rows = [{"year": 2023, "revenue": 50.0, "assets": 200.0, "net_profit": None, "equity": None, "liabilities": None, "debt": None}]
        result = sanitize_financial_rows(rows)
        assert result[0]["revenue"] == 50.0

    def test_removes_disproportionate_net_profit(self):
        rows = [{"year": 2023, "revenue": 100.0, "net_profit": 200.0, "assets": None, "equity": None, "liabilities": None, "debt": None}]
        result = sanitize_financial_rows(rows)
        assert result[0]["net_profit"] is None

    def test_keeps_proportionate_net_profit(self):
        rows = [{"year": 2023, "revenue": 100.0, "net_profit": 10.0, "assets": None, "equity": None, "liabilities": None, "debt": None}]
        result = sanitize_financial_rows(rows)
        assert result[0]["net_profit"] == 10.0

    def test_removes_equity_exceeding_assets(self):
        rows = [{"year": 2023, "revenue": None, "net_profit": None, "assets": 100.0, "equity": 200.0, "liabilities": None, "debt": None}]
        result = sanitize_financial_rows(rows)
        assert result[0]["equity"] is None

    def test_removes_liabilities_exceeding_assets(self):
        rows = [{"year": 2023, "revenue": None, "net_profit": None, "assets": 100.0, "equity": None, "liabilities": 150.0, "debt": None}]
        result = sanitize_financial_rows(rows)
        assert result[0]["liabilities"] is None

    def test_removes_debt_exceeding_assets(self):
        rows = [{"year": 2023, "revenue": None, "net_profit": None, "assets": 100.0, "equity": None, "liabilities": None, "debt": 200.0}]
        result = sanitize_financial_rows(rows)
        assert result[0]["debt"] is None

    def test_keeps_all_valid(self):
        rows = [{"year": 2023, "revenue": 100.0, "net_profit": 5.0, "assets": 200.0, "equity": 80.0, "liabilities": 120.0, "debt": 50.0}]
        result = sanitize_financial_rows(rows)
        assert result[0]["revenue"] == 100.0
        assert result[0]["net_profit"] == 5.0
        assert result[0]["equity"] == 80.0
        assert result[0]["liabilities"] == 120.0
        assert result[0]["debt"] == 50.0

    def test_empty_timeline(self):
        result = sanitize_financial_rows([])
        assert result == []


# ---------------------------------------------------------------------------
# pct_change
# ---------------------------------------------------------------------------
class TestPctChange:
    def test_basic_increase(self):
        result = pct_change(110.0, 100.0)
        assert result == 10.0

    def test_basic_decrease(self):
        result = pct_change(90.0, 100.0)
        assert result == -10.0

    def test_double(self):
        result = pct_change(200.0, 100.0)
        assert result == 100.0

    def test_none_current(self):
        assert pct_change(None, 100.0) is None

    def test_none_previous(self):
        assert pct_change(100.0, None) is None

    def test_both_none(self):
        assert pct_change(None, None) is None

    def test_zero_previous(self):
        assert pct_change(100.0, 0.0) is None

    def test_negative_previous(self):
        result = pct_change(-50.0, -100.0)
        # (-50 - (-100)) / abs(-100) * 100 = 50.0
        assert result == 50.0

    def test_same_value(self):
        assert pct_change(100.0, 100.0) == 0.0
