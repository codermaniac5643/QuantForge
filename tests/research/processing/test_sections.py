# tests/research/processing/test_sections.py
"""
Tests for src/quantforge/research/processing/sections.py

No external dependencies — pure text inputs.
"""

from __future__ import annotations

import pytest

from quantforge.research.processing.sections import (
    SectionEntry,
    extract_sections,
    _is_heading,
)


# ---------------------------------------------------------------------------
# _is_heading unit tests
# ---------------------------------------------------------------------------


class TestIsHeading:
    def test_known_heading_abstract(self):
        assert _is_heading("Abstract") is True

    def test_known_heading_introduction(self):
        assert _is_heading("Introduction") is True

    def test_known_heading_conclusion(self):
        assert _is_heading("Conclusion") is True

    def test_known_heading_references(self):
        assert _is_heading("References") is True

    def test_known_heading_case_insensitive(self):
        assert _is_heading("ABSTRACT") is True

    def test_numeric_prefix_section(self):
        assert _is_heading("1. Introduction") is True

    def test_numeric_prefix_subsection(self):
        assert _is_heading("2.3 Methodology") is True

    def test_all_caps_heading(self):
        assert _is_heading("RESULTS") is True

    def test_title_case_two_words(self):
        assert _is_heading("Related Work") is True

    def test_empty_string(self):
        assert _is_heading("") is False

    def test_long_line_rejected(self):
        # 15 words — over MAX_HEADING_WORDS
        long_line = " ".join(["Word"] * 15)
        assert _is_heading(long_line) is False

    def test_sentence_ending_period_rejected(self):
        # Looks like a normal sentence
        assert _is_heading("We study momentum factors.") is False

    def test_page_marker_not_heading(self):
        assert _is_heading("--- Page 3 ---") is False


# ---------------------------------------------------------------------------
# extract_sections — empty / no headings
# ---------------------------------------------------------------------------


class TestExtractSectionsEmpty:
    def test_empty_string_returns_empty(self):
        assert extract_sections("") == []

    def test_no_headings_returns_empty(self):
        text = "This is just some prose.\nNo headings here at all.\n"
        assert extract_sections(text) == []

    def test_only_page_markers_returns_empty(self):
        text = "--- Page 1 ---\n--- Page 2 ---\n"
        assert extract_sections(text) == []


# ---------------------------------------------------------------------------
# extract_sections — basic heading detection
# ---------------------------------------------------------------------------


class TestExtractSectionsBasic:
    def _make_text(self) -> str:
        return (
            "--- Page 1 ---\n"
            "Abstract\n"
            "This paper studies momentum.\n"
            "--- Page 2 ---\n"
            "Introduction\n"
            "Momentum investing is well documented.\n"
            "--- Page 3 ---\n"
            "Conclusion\n"
            "We confirm the momentum effect.\n"
            "References\n"
            "Jegadeesh and Titman (1993).\n"
        )

    def test_returns_list_of_section_entries(self):
        sections = extract_sections(self._make_text())
        assert isinstance(sections, list)
        assert len(sections) >= 1

    def test_first_heading_is_abstract(self):
        sections = extract_sections(self._make_text())
        assert sections[0]["heading"] == "Abstract"

    def test_introduction_detected(self):
        sections = extract_sections(self._make_text())
        headings = [s["heading"] for s in sections]
        assert "Introduction" in headings

    def test_conclusion_detected(self):
        sections = extract_sections(self._make_text())
        headings = [s["heading"] for s in sections]
        assert "Conclusion" in headings

    def test_content_under_abstract(self):
        sections = extract_sections(self._make_text())
        abstract = next(s for s in sections if s["heading"] == "Abstract")
        assert "momentum" in abstract["content"].lower()

    def test_page_markers_not_in_content(self):
        sections = extract_sections(self._make_text())
        for s in sections:
            assert "--- Page" not in s["content"]

    def test_page_number_tracked(self):
        sections = extract_sections(self._make_text())
        abstract = next(s for s in sections if s["heading"] == "Abstract")
        assert abstract["page"] == 1
        intro = next(s for s in sections if s["heading"] == "Introduction")
        assert intro["page"] == 2

    def test_numeric_prefix_headings(self):
        text = (
            "--- Page 1 ---\n"
            "1. Introduction\n"
            "Some intro text here.\n"
            "2. Methodology\n"
            "Some methods text here.\n"
        )
        sections = extract_sections(text)
        headings = [s["heading"] for s in sections]
        assert "1. Introduction" in headings
        assert "2. Methodology" in headings

    def test_all_caps_heading(self):
        text = "--- Page 1 ---\nRESULTS\nWe found significant alpha.\n"
        sections = extract_sections(text)
        headings = [s["heading"] for s in sections]
        assert "RESULTS" in headings

    def test_section_entry_has_required_keys(self):
        text = "Abstract\nSome text.\n"
        sections = extract_sections(text)
        assert len(sections) >= 1
        for s in sections:
            assert "heading" in s
            assert "page" in s
            assert "content" in s

