# tests/research/normalization/test_text.py
"""
Unit tests for text normalization utilities (title, DOI, arXiv ID).
"""

import pytest

from quantforge.research.normalization.text import (
    normalize_doi,
    normalize_arxiv_id,
    normalize_title,
)


# ---------------------------------------------------------------------------
# normalize_title
# ---------------------------------------------------------------------------

class TestNormalizeTitle:
    def test_basic_lowercase(self):
        assert normalize_title("Momentum Investing") == "momentum investing"

    def test_strips_punctuation(self):
        result = normalize_title("Cross-Sectional Momentum: A Study!")
        assert ":" not in result
        assert "-" not in result
        assert "!" not in result

    def test_collapses_whitespace(self):
        result = normalize_title("  Momentum   Investing  ")
        assert result == "momentum   investing".replace("   ", " ")
        # Ensure no leading/trailing whitespace
        assert result == result.strip()

    def test_unicode_nfc_normalization(self):
        # Both should normalize to the same string
        title_a = "M\u00f6bius Strip Research"       # precomposed ö
        title_b = "Mo\u0308bius Strip Research"      # decomposed o + combining umlaut
        assert normalize_title(title_a) == normalize_title(title_b)

    def test_empty_string_returns_empty(self):
        assert normalize_title("") == ""

    def test_all_punctuation_becomes_spaces(self):
        result = normalize_title("...-...!!!")
        assert result == "" or result.strip() == ""

    def test_equal_titles_produce_equal_keys(self):
        t1 = "Cross-Sectional Momentum in Equity Markets"
        t2 = "cross sectional momentum in equity markets"
        assert normalize_title(t1) == normalize_title(t2)

    def test_different_titles_produce_different_keys(self):
        t1 = "Momentum Investing in Equities"
        t2 = "Mean Reversion Strategies"
        assert normalize_title(t1) != normalize_title(t2)


# ---------------------------------------------------------------------------
# normalize_doi
# ---------------------------------------------------------------------------

class TestNormalizeDoi:
    def test_strips_https_prefix(self):
        result = normalize_doi("https://doi.org/10.1234/example")
        assert result == "10.1234/example"

    def test_strips_http_prefix(self):
        result = normalize_doi("http://doi.org/10.1234/example")
        assert result == "10.1234/example"

    def test_strips_dx_prefix(self):
        result = normalize_doi("https://dx.doi.org/10.1234/example")
        assert result == "10.1234/example"

    def test_strips_doi_colon_prefix(self):
        result = normalize_doi("doi:10.1234/example")
        assert result == "10.1234/example"

    def test_bare_doi_lowercased(self):
        result = normalize_doi("10.1234/EXAMPLE")
        assert result == "10.1234/example"

    def test_none_returns_none(self):
        assert normalize_doi(None) is None

    def test_empty_string_returns_none(self):
        assert normalize_doi("") is None

    def test_whitespace_only_returns_none(self):
        assert normalize_doi("   ") is None

    def test_equal_dois_produce_equal_keys(self):
        a = "https://doi.org/10.1234/EXAMPLE"
        b = "10.1234/example"
        assert normalize_doi(a) == normalize_doi(b)

    def test_different_dois_produce_different_keys(self):
        a = "10.1234/paper_a"
        b = "10.5678/paper_b"
        assert normalize_doi(a) != normalize_doi(b)


# ---------------------------------------------------------------------------
# normalize_arxiv_id
# ---------------------------------------------------------------------------

class TestNormalizeArxivId:
    def test_strips_url_prefix(self):
        result = normalize_arxiv_id("https://arxiv.org/abs/2401.00001v1")
        assert result == "2401.00001"

    def test_strips_http_url_prefix(self):
        result = normalize_arxiv_id("http://arxiv.org/abs/2401.00001")
        assert result == "2401.00001"

    def test_strips_version_suffix(self):
        result = normalize_arxiv_id("2401.00001v2")
        assert result == "2401.00001"

    def test_strips_uppercase_version_suffix(self):
        result = normalize_arxiv_id("2401.00001V3")
        assert result == "2401.00001"

    def test_bare_id_no_version(self):
        result = normalize_arxiv_id("2401.00001")
        assert result == "2401.00001"

    def test_lowercases_result(self):
        result = normalize_arxiv_id("QUANT-PH/9901001V1")
        assert result == result.lower()

    def test_none_returns_none(self):
        assert normalize_arxiv_id(None) is None

    def test_empty_string_returns_none(self):
        assert normalize_arxiv_id("") is None

    def test_equal_ids_produce_equal_keys(self):
        a = "https://arxiv.org/abs/2401.00001v1"
        b = "2401.00001v2"
        assert normalize_arxiv_id(a) == normalize_arxiv_id(b)

    def test_different_ids_produce_different_keys(self):
        a = "2401.00001"
        b = "2401.00002"
        assert normalize_arxiv_id(a) != normalize_arxiv_id(b)

