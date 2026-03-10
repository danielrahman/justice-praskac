"""Tests for justice.documents scoring and classification functions."""
from __future__ import annotations

import tempfile
import threading
import time
from pathlib import Path

import pytest

import justice.documents as documents_module
from justice.documents import _find_pdftoppm_image, financial_doc_score, is_financial_document, pick_recent_financial_docs


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


# ---------------------------------------------------------------------------
# _find_pdftoppm_image
# ---------------------------------------------------------------------------
class TestFindPdftoppmImage:
    def test_zero_padded_single_digit_page(self):
        """pdftoppm creates page1-01.png for page 1 of a 10+ page doc."""
        with tempfile.TemporaryDirectory() as tmpdir:
            prefix = Path(tmpdir) / "page1"
            (Path(tmpdir) / "page1-01.png").touch()
            result = _find_pdftoppm_image(prefix, 1)
            assert result.name == "page1-01.png"

    def test_non_padded_single_digit_page(self):
        """pdftoppm creates page1-1.png for page 1 of a <10 page doc."""
        with tempfile.TemporaryDirectory() as tmpdir:
            prefix = Path(tmpdir) / "page1"
            (Path(tmpdir) / "page1-1.png").touch()
            result = _find_pdftoppm_image(prefix, 1)
            assert result.name == "page1-1.png"

    def test_double_digit_page(self):
        """pdftoppm creates page10-10.png for page 10."""
        with tempfile.TemporaryDirectory() as tmpdir:
            prefix = Path(tmpdir) / "page10"
            (Path(tmpdir) / "page10-10.png").touch()
            result = _find_pdftoppm_image(prefix, 10)
            assert result.name == "page10-10.png"

    def test_triple_padded_page(self):
        """pdftoppm creates page1-001.png for page 1 of a 100+ page doc."""
        with tempfile.TemporaryDirectory() as tmpdir:
            prefix = Path(tmpdir) / "page1"
            (Path(tmpdir) / "page1-001.png").touch()
            result = _find_pdftoppm_image(prefix, 1)
            assert result.name == "page1-001.png"

    def test_no_file_returns_fallback(self):
        """When no file exists, returns the non-padded fallback path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            prefix = Path(tmpdir) / "page5"
            result = _find_pdftoppm_image(prefix, 5)
            assert result.name == "page5-5.png"

    def test_does_not_match_other_page_prefix(self):
        """page1-*.png must not match page10-10.png."""
        with tempfile.TemporaryDirectory() as tmpdir:
            prefix = Path(tmpdir) / "page1"
            (Path(tmpdir) / "page10-10.png").touch()
            result = _find_pdftoppm_image(prefix, 1)
            # Should return fallback, not page10's file
            assert result.name == "page1-1.png"


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
