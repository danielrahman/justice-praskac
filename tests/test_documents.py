"""Tests for justice.documents scoring and classification functions."""
from __future__ import annotations

import threading
import time

import pytest

import justice.documents as documents_module
from justice.documents import financial_doc_score, is_financial_document, pick_recent_financial_docs


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


class TestPickRecentFinancialDocs:
    def test_parallel_detail_enrichment_preserves_sorted_order(self, monkeypatch):
        monkeypatch.setattr(documents_module, "JUSTICE_DOCUMENT_WORKERS", 4)
        docs = [
            {"document_number": "C 1/SL1", "type": "Výroční zpráva [2024]", "pages": 20, "years": [2024], "detail_url": "https://or.justice.cz/doc/1", "filed_date": "2025-01-02"},
            {"document_number": "C 1/SL2", "type": "Zpráva auditora [2024]", "pages": 6, "years": [2024], "detail_url": "https://or.justice.cz/doc/2", "filed_date": "2025-01-01"},
            {"document_number": "C 1/SL3", "type": "Účetní závěrka [2023]", "pages": 12, "years": [2023], "detail_url": "https://or.justice.cz/doc/3", "filed_date": "2024-01-01"},
        ]
        started_together = threading.Event()
        state = {"active": 0, "peak": 0}
        lock = threading.Lock()
        delays = {
            "https://or.justice.cz/doc/1": 0.04,
            "https://or.justice.cz/doc/2": 0.01,
            "https://or.justice.cz/doc/3": 0.02,
        }

        def fake_parse_document_detail(url: str, force_refresh: bool = False, parent_type: str | None = None):
            with lock:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
                if state["active"] >= 2:
                    started_together.set()
            assert started_together.wait(0.5)
            time.sleep(delays[url])
            with lock:
                state["active"] -= 1
            return {
                "detail_url": url,
                "pdf_candidates": [{"label": f"{url}.pdf", "url": f"{url}.pdf", "pdf_index": 0}],
                "download_links": [],
            }

        monkeypatch.setattr(documents_module, "parse_document_detail", fake_parse_document_detail)

        selected = pick_recent_financial_docs(docs, max_years=2)

        assert state["peak"] >= 2
        assert [doc["detail_url"] for doc in selected] == [
            "https://or.justice.cz/doc/1",
            "https://or.justice.cz/doc/2",
            "https://or.justice.cz/doc/3",
        ]
        assert [doc["candidate_file_count"] for doc in selected] == [1, 1, 1]
