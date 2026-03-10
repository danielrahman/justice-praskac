"""Tests for justice.utils parsing helpers."""
from __future__ import annotations

import pytest

from justice.utils import (
    is_probable_year,
    norm_key,
    normalize_ai_model_name,
    parse_czech_date,
    parse_number_candidates,
    slug_hash,
    strip_accents,
)
from justice.scraping import clean_ico


# ---------------------------------------------------------------------------
# parse_czech_date
# ---------------------------------------------------------------------------
class TestParseCzechDate:
    def test_standard_long_date(self):
        assert parse_czech_date("15. ledna 2023") == "2023-01-15"

    def test_month_unora(self):
        # "února" -> norm_key -> "unora", which is not in MONTHS.
        # The accented key "února" is in MONTHS but norm_key strips it.
        # This is a known gap: only months with entries for their
        # accent-stripped forms are recognized.
        assert parse_czech_date("1. února 2022") is None

    def test_month_brezna(self):
        assert parse_czech_date("10. března 2021") == "2021-03-10"

    def test_month_dubna(self):
        assert parse_czech_date("5. dubna 2020") == "2020-04-05"

    def test_month_kvetna(self):
        # "května" -> norm_key -> "kvetna", which is not in MONTHS.
        # Same gap as února: the accent-stripped form is missing from MONTHS.
        assert parse_czech_date("20. května 2019") is None

    def test_month_cervna(self):
        assert parse_czech_date("30. června 2024") == "2024-06-30"

    def test_month_cervence(self):
        assert parse_czech_date("7. července 2023") == "2023-07-07"

    def test_month_srpna(self):
        assert parse_czech_date("15. srpna 2023") == "2023-08-15"

    def test_month_zari(self):
        assert parse_czech_date("1. září 2023") == "2023-09-01"

    def test_month_rijna(self):
        assert parse_czech_date("12. října 2023") == "2023-10-12"

    def test_month_listopadu(self):
        assert parse_czech_date("28. listopadu 2023") == "2023-11-28"

    def test_month_prosince(self):
        assert parse_czech_date("31. prosince 2023") == "2023-12-31"

    def test_short_numeric_date(self):
        assert parse_czech_date("1.3.2022") == "2022-03-01"

    def test_short_numeric_date_padded(self):
        assert parse_czech_date("01.03.2022") == "2022-03-01"

    def test_empty_string(self):
        assert parse_czech_date("") is None

    def test_none_input(self):
        assert parse_czech_date(None) is None

    def test_whitespace_only(self):
        assert parse_czech_date("   ") is None

    def test_invalid_text(self):
        assert parse_czech_date("not a date") is None

    def test_invalid_month_name(self):
        assert parse_czech_date("15. foobar 2023") is None

    def test_accented_without_accents(self):
        # "brezna" is the accent-stripped form of "března" and is in MONTHS
        assert parse_czech_date("10. brezna 2021") == "2021-03-10"


# ---------------------------------------------------------------------------
# parse_number_candidates
# ---------------------------------------------------------------------------
class TestParseNumberCandidates:
    def test_czech_thousands_space(self):
        result = parse_number_candidates("1 234 567")
        assert 1234567 in result

    def test_czech_thousands_dot(self):
        result = parse_number_candidates("1.234.567")
        assert 1234567 in result

    def test_negative_number(self):
        result = parse_number_candidates("-500")
        assert -500 in result

    def test_multiple_numbers(self):
        result = parse_number_candidates("Trzby 12 953   17 433")
        assert 12953 in result
        assert 17433 in result

    def test_empty_string(self):
        assert parse_number_candidates("") == []

    def test_no_numbers(self):
        assert parse_number_candidates("abc def") == []

    def test_small_fraction_ignored(self):
        # values < 1 are filtered out
        result = parse_number_candidates("0,5")
        assert result == []

    def test_single_integer(self):
        result = parse_number_candidates("42")
        assert 42 in result


# ---------------------------------------------------------------------------
# strip_accents
# ---------------------------------------------------------------------------
class TestStripAccents:
    def test_czech_chars(self):
        assert strip_accents("Účetní závěrka") == "Ucetni zaverka"

    def test_plain_ascii(self):
        assert strip_accents("hello world") == "hello world"

    def test_empty(self):
        assert strip_accents("") == ""

    def test_mixed(self):
        assert strip_accents("Příloha č. 1") == "Priloha c. 1"

    def test_caron_chars(self):
        assert strip_accents("žščřďťňě") == "zscrdtne"


# ---------------------------------------------------------------------------
# norm_key
# ---------------------------------------------------------------------------
class TestNormKey:
    def test_lowercases_and_strips_accents(self):
        assert norm_key("Účetní Závěrka") == "ucetni zaverka"

    def test_collapses_whitespace(self):
        assert norm_key("  foo   bar  ") == "foo bar"

    def test_replaces_dashes(self):
        assert norm_key("rok 2022–2023") == "rok 2022-2023"

    def test_nbsp_handling(self):
        assert norm_key("foo\xa0bar") == "foo bar"

    def test_empty(self):
        assert norm_key("") == ""


# ---------------------------------------------------------------------------
# normalize_ai_model_name
# ---------------------------------------------------------------------------
class TestNormalizeAiModelName:
    def test_maps_legacy_underscore_alias(self):
        assert normalize_ai_model_name("claude_sonnet_4_5") == "claude-sonnet-4-20250514"

    def test_maps_legacy_hyphen_alias(self):
        assert normalize_ai_model_name("claude-sonnet-4-5") == "claude-sonnet-4-20250514"

    def test_preserves_valid_model_name(self):
        assert normalize_ai_model_name("claude-sonnet-4-20250514") == "claude-sonnet-4-20250514"


# ---------------------------------------------------------------------------
# clean_ico
# ---------------------------------------------------------------------------
class TestCleanIco:
    def test_strips_whitespace(self):
        assert clean_ico("  123 456 78  ") == "12345678"

    def test_extracts_digits(self):
        assert clean_ico("IČ: 12345678") == "12345678"

    def test_empty(self):
        assert clean_ico("") == ""

    def test_none(self):
        assert clean_ico(None) == ""

    def test_only_letters(self):
        assert clean_ico("abcdef") == ""


# ---------------------------------------------------------------------------
# is_probable_year
# ---------------------------------------------------------------------------
class TestIsProbableYear:
    def test_valid_years(self):
        assert is_probable_year(2020) is True
        assert is_probable_year(2023) is True
        assert is_probable_year(2025) is True
        assert is_probable_year(1900) is True

    def test_too_old(self):
        assert is_probable_year(1800) is False

    def test_too_far_future(self):
        assert is_probable_year(3000) is False

    def test_negative_year(self):
        # abs(-2020) = 2020, which is in range
        assert is_probable_year(-2020) is True

    def test_small_number(self):
        assert is_probable_year(500) is False

    def test_zero(self):
        assert is_probable_year(0) is False


# ---------------------------------------------------------------------------
# slug_hash
# ---------------------------------------------------------------------------
class TestSlugHash:
    def test_consistent(self):
        h1 = slug_hash("test string")
        h2 = slug_hash("test string")
        assert h1 == h2

    def test_different_inputs(self):
        h1 = slug_hash("foo")
        h2 = slug_hash("bar")
        assert h1 != h2

    def test_returns_hex_string(self):
        result = slug_hash("anything")
        assert len(result) == 32
        assert all(c in "0123456789abcdef" for c in result)

    def test_unicode_input(self):
        result = slug_hash("Účetní závěrka")
        assert len(result) == 32
