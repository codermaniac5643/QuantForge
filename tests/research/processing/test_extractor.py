# tests/research/processing/test_extractor.py
"""
Tests for src/quantforge/research/processing/extractor.py

All tests use either the synthetic sample.pdf fixture or in-memory
bytes — no network, no DB.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import fitz
import pytest

from quantforge.research.processing.extractor import (
    ExtractionResult,
    PROCESSING_VERSION,
    MIN_CHARS_THRESHOLD,
    detect_extraction_failure,
    extract_text,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

FIXTURE_PDF = Path(__file__).parent / "sample.pdf"


def _make_pdf_with_text(text: str, path: Path) -> None:
    """Write a single-page PDF containing *text* to *path*."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 72), text, fontsize=10)
    doc.save(str(path))
    doc.close()


def _make_empty_pdf(path: Path) -> None:
    """Write a valid PDF with one page but no text."""
    doc = fitz.open()
    doc.new_page()  # blank — no text
    doc.save(str(path))
    doc.close()


# ---------------------------------------------------------------------------
# detect_extraction_failure
# ---------------------------------------------------------------------------


class TestDetectExtractionFailure:
    def test_zero_pages_always_fails(self):
        failed, reason = detect_extraction_failure("some text", page_count=0)
        assert failed is True
        assert reason is not None

    def test_enough_chars_passes(self):
        text = "a" * (MIN_CHARS_THRESHOLD + 1)
        failed, reason = detect_extraction_failure(text, page_count=1)
        assert failed is False
        assert reason is None

    def test_too_few_chars_fails(self):
        text = "hi"
        failed, reason = detect_extraction_failure(text, page_count=1)
        assert failed is True
        assert "non-whitespace" in reason

    def test_whitespace_only_fails(self):
        text = "   \n\n\t   "
        failed, reason = detect_extraction_failure(text, page_count=5)
        assert failed is True

    def test_exact_threshold_passes(self):
        # Exactly MIN_CHARS_THRESHOLD chars → passes (threshold is strict <)
        text = "x" * MIN_CHARS_THRESHOLD
        failed, _ = detect_extraction_failure(text, page_count=1)
        assert failed is False

    def test_one_above_threshold_passes(self):
        text = "x" * (MIN_CHARS_THRESHOLD + 1)
        failed, _ = detect_extraction_failure(text, page_count=1)
        assert failed is False


# ---------------------------------------------------------------------------
# extract_text — file-not-found and invalid inputs
# ---------------------------------------------------------------------------


class TestExtractTextFileErrors:
    def test_missing_file_returns_failed(self, tmp_path):
        result = extract_text(tmp_path / "nonexistent.pdf")
        assert result.extraction_failed is True
        assert result.failure_reason is not None
        assert result.page_count == 0

    def test_path_is_directory_returns_failed(self, tmp_path):
        result = extract_text(tmp_path)  # directory, not file
        assert result.extraction_failed is True

    def test_non_pdf_file_returns_failed(self, tmp_path):
        fake = tmp_path / "fake.pdf"
        fake.write_bytes(b"not a pdf at all")
        result = extract_text(fake)
        assert result.extraction_failed is True


# ---------------------------------------------------------------------------
# extract_text — empty PDF
# ---------------------------------------------------------------------------


class TestExtractTextEmptyPdf:
    def test_blank_pdf_reports_failure(self, tmp_path):
        blank = tmp_path / "blank.pdf"
        _make_empty_pdf(blank)
        result = extract_text(blank)
        assert result.extraction_failed is True
        assert result.page_count == 1  # the page exists

    def test_blank_pdf_returns_extraction_result_type(self, tmp_path):
        blank = tmp_path / "blank.pdf"
        _make_empty_pdf(blank)
        result = extract_text(blank)
        assert isinstance(result, ExtractionResult)


# ---------------------------------------------------------------------------
# extract_text — good PDF (fixture)
# ---------------------------------------------------------------------------


class TestExtractTextGoodPdf:
    def test_returns_extraction_result(self):
        result = extract_text(FIXTURE_PDF)
        assert isinstance(result, ExtractionResult)

    def test_not_failed(self):
        result = extract_text(FIXTURE_PDF)
        assert result.extraction_failed is False
        assert result.failure_reason is None

    def test_page_count_matches_fixture(self):
        result = extract_text(FIXTURE_PDF)
        # sample.pdf was created with 3 pages
        assert result.page_count == 3

    def test_pages_list_length(self):
        result = extract_text(FIXTURE_PDF)
        assert len(result.pages) == result.page_count

    def test_pages_are_1_based(self):
        result = extract_text(FIXTURE_PDF)
        page_numbers = [p.page_number for p in result.pages]
        assert page_numbers == list(range(1, result.page_count + 1))

    def test_full_text_contains_known_words(self):
        result = extract_text(FIXTURE_PDF)
        assert "Momentum" in result.full_text or "momentum" in result.full_text.lower()

    def test_full_text_contains_page_markers(self):
        result = extract_text(FIXTURE_PDF)
        assert "--- Page 1 ---" in result.full_text
        assert "--- Page 2 ---" in result.full_text

    def test_char_count_positive(self):
        result = extract_text(FIXTURE_PDF)
        assert result.char_count > 0

    def test_processing_version(self):
        result = extract_text(FIXTURE_PDF)
        assert result.processing_version == PROCESSING_VERSION

    def test_single_page_pdf(self, tmp_path):
        p = tmp_path / "one_page.pdf"
        _make_pdf_with_text("Hello world, this is a test document with enough text.", p)
        result = extract_text(p)
        assert result.page_count == 1
        assert result.extraction_failed is False
        assert "Hello" in result.full_text
