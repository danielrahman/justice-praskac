"""Tests for justice.documents scoring and classification functions."""
from __future__ import annotations

import pytest

from justice.documents import financial_doc_score, is_financial_document


# ---------------------------------------------------------------------------
# is_financial_document
# ---------------------------------------------------------------------------
class TestIsFinancialDocument:
    def test_ucetni_zaverka(self):
        doc = {"type": "Účetní závěrka [2023]"}
        assert is_financial_document(doc) is True

    def test_vyrocni_zprava(self):
        doc = {"type": "Výroční zpráva [2022]"}
        assert is_financial_document(doc) is True

    def test_zprava_auditora(self):
        doc = {"type": "Zpráva auditora [2023]"}
        assert is_financial_document(doc) is True

    def test_zprava_o_vztazich(self):
        doc = {"type": "Zpráva o vztazích [2023]"}
        assert is_financial_document(doc) is True

    def test_plna_moc_is_not_financial(self):
        doc = {"type": "Plná moc"}
        assert is_financial_document(doc) is False

    def test_spolecenska_smlouva_is_not_financial(self):
        doc = {"type": "Společenská smlouva"}
        assert is_financial_document(doc) is False

    def test_empty_type(self):
        doc = {"type": ""}
        assert is_financial_document(doc) is False

    def test_missing_type(self):
        doc = {}
        assert is_financial_document(doc) is False

    def test_ascii_variant(self):
        # The accent-stripped variant should also match
        doc = {"type": "ucetni zaverka [2023]"}
        assert is_financial_document(doc) is True

    def test_vyrocni_zprava_ascii(self):
        doc = {"type": "vyrocni zprava [2021]"}
        assert is_financial_document(doc) is True


# ---------------------------------------------------------------------------
# financial_doc_score
# ---------------------------------------------------------------------------
class TestFinancialDocScore:
    def test_vyrocni_zprava_scores_high(self):
        doc = {"type": "Výroční zpráva [2023]", "pages": 20, "years": [2023]}
        score = financial_doc_score(doc)
        assert score > 80

    def test_ucetni_zaverka_scores_above_baseline(self):
        doc = {"type": "Účetní závěrka [2023]", "pages": 10, "years": [2023]}
        score = financial_doc_score(doc)
        assert score > 30

    def test_zprava_auditora_scores_positive(self):
        doc = {"type": "Zpráva auditora [2023]", "pages": 5, "years": [2023]}
        score = financial_doc_score(doc)
        assert score > 30

    def test_priloha_reduces_score(self):
        doc_with_priloha = {"type": "Příloha k účetní závěrce [2023]", "pages": 10, "years": [2023]}
        doc_without_priloha = {"type": "Účetní závěrka [2023]", "pages": 10, "years": [2023]}
        score_with = financial_doc_score(doc_with_priloha)
        score_without = financial_doc_score(doc_without_priloha)
        assert score_with < score_without

    def test_zero_pages_penalized(self):
        doc = {"type": "Účetní závěrka [2023]", "pages": 0, "years": [2023]}
        score = financial_doc_score(doc)
        # The -120 penalty for 0 pages should make this lower
        assert score < 0

    def test_more_pages_score_higher(self):
        doc_few = {"type": "Účetní závěrka [2023]", "pages": 5, "years": [2023]}
        doc_many = {"type": "Účetní závěrka [2023]", "pages": 50, "years": [2023]}
        assert financial_doc_score(doc_many) > financial_doc_score(doc_few)

    def test_multiple_years_bonus(self):
        doc_one_year = {"type": "Účetní závěrka [2023]", "pages": 10, "years": [2023]}
        doc_two_years = {"type": "Účetní závěrka [2023] [2022]", "pages": 10, "years": [2023, 2022]}
        assert financial_doc_score(doc_two_years) > financial_doc_score(doc_one_year)

    def test_unknown_type(self):
        doc = {"type": "Nějaký neznámý dokument", "pages": 10, "years": []}
        score = financial_doc_score(doc)
        # Should just be page count without bonuses
        assert score == 10

    def test_vyrocni_zprava_beats_zaverka(self):
        vyrocni = {"type": "Výroční zpráva [2023]", "pages": 10, "years": [2023]}
        zaverka = {"type": "Účetní závěrka [2023]", "pages": 10, "years": [2023]}
        assert financial_doc_score(vyrocni) > financial_doc_score(zaverka)
