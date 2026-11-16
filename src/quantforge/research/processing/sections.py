# src/quantforge/research/processing/sections.py
"""
Basic section-heading detection from extracted PDF text.

Responsibilities:
  - Parse the flat text produced by extractor.py into logical sections.
  - Return a list of SectionEntry dicts suitable for JSON serialisation.
  - Keep heuristics simple and deterministic — no ML, no LLM.

Heading detection strategy:
  - A line is treated as a heading when it is:
      * Short (≤ MAX_HEADING_WORDS words).
      * Not ending with a period or comma.
      * Either ALL-CAPS, Title-Cased (every word capitalised), or starts with
        a numeric section marker such as "1.", "2.1", "A.", "I.".
  - Abstract, Introduction, Conclusion, References and other common
    academic headings are matched directly (case-insensitive).
  - Lines that look like page-boundary markers ("--- Page N ---") are used
    to track the current page number; they are not emitted as headings.

What it does NOT do:
  - Identify figures, tables, or equations.
  - Rank or weight sections by importance.
  - Persist anything to disk.
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import TypedDict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_HEADING_WORDS = 12

# Common academic section names (case-insensitive match)
KNOWN_HEADINGS: frozenset[str] = frozenset(
    {
        "abstract",
        "introduction",
        "background",
        "related work",
        "related works",
        "literature review",
        "methodology",
        "methods",
        "method",
        "data",
        "data and methods",
        "data and methodology",
        "experiments",
        "experiment",
        "experimental setup",
        "results",
        "results and discussion",
        "discussion",
        "conclusion",
        "conclusions",
        "conclusion and future work",
        "future work",
        "references",
        "bibliography",
        "appendix",
        "acknowledgements",
        "acknowledgments",
        "notation",
        "problem formulation",
        "problem statement",
        "model",
        "evaluation",
        "analysis",
        "implementation",
        "limitations",
        "ethical considerations",
    }
)

# Lines to never treat as headings regardless of formatting
_HEADING_BLOCKLIST_RE = re.compile(
    r'^(working\s+manuscript|working\s+paper|last\s+updated|'
    r'this\s+evaluation|submitted\s+to|under\s+review|'
    r'prepublication\s+draft|framing\s+effects|'
    r'collusive\s+behavior)',  # paper-specific subtitle fragments
    re.IGNORECASE,
)

# Numeric prefix: "1.", "1.1", "1.1.1", "A.", "I.", "II."
_NUMERIC_PREFIX_RE = re.compile(
    r"^(?:\d+(?:\.\d+)*\.?|[A-Z]\.?|(?:I{1,3}|IV|VI{0,3}|IX|X{1,3})\.?)\s"
)

# Page boundary line produced by extractor.py
_PAGE_MARKER_RE = re.compile(r"^---\s*Page\s+(\d+)\s*---\s*$")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


class SectionEntry(TypedDict):
    """One detected section, as stored in sections.json."""

    heading: str
    page: int        # 1-based page where the heading was found
    content: str     # text under this heading until the next heading


@dataclass
class _HeadingCandidate:
    heading: str
    page: int
    start_line: int


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_sections(text: str) -> list[SectionEntry]:
    """
    Parse *text* (the full_text from ExtractionResult) into sections.

    Args:
        text: Full extracted text, including "--- Page N ---" markers.

    Returns:
        List of SectionEntry dicts.  An empty list is returned when no
        headings are detected, not an error.
    """
    lines = text.splitlines()
    candidates: list[_HeadingCandidate] = []
    current_page = 1

    for idx, raw_line in enumerate(lines):
        line = raw_line.strip()

        # Track page boundaries
        page_match = _PAGE_MARKER_RE.match(line)
        if page_match:
            current_page = int(page_match.group(1))
            continue        

        if _is_heading(line):
            candidates.append(
                _HeadingCandidate(heading=line, page=current_page, start_line=idx)
            )

    if not candidates:
        logger.debug("No section headings detected in text (%d lines)", len(lines))
        return []

    # Build section entries: content = lines between this heading and next
    sections: list[SectionEntry] = []
    for i, cand in enumerate(candidates):
        next_start = candidates[i + 1].start_line if i + 1 < len(candidates) else len(lines)
        content_lines = lines[cand.start_line + 1 : next_start]
        # Strip page markers from content
        content_lines = [
            ln for ln in content_lines if not _PAGE_MARKER_RE.match(ln.strip())
        ]
        content = "\n".join(content_lines).strip()

        # Skip TOC entries — content is just a page number (1-3 chars)
        if len(content) <= 3 and content.strip().isdigit():
            continue

        # Skip TOC entries — content consists mostly of dot leaders and page numbers
        # e.g. "Experiment B . . . . . . 9\n4.2 ..."
        toc_lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
        if toc_lines and all(
            re.match(r'^[\d\.\s]+$', ln) or  # pure numbers/dots
            ln.count('.') > len(ln) * 0.3    # more than 30% dots (dot leaders)
            for ln in toc_lines[:5]          # check first 5 lines only
        ):
            continue

        # Skip duplicate headings — keep only the first occurrence
        # with meaningful content
        if any(s["heading"].lower() == cand.heading.lower() for s in sections):
            if len(content) > len(next((s["content"] for s in sections
                                        if s["heading"].lower() == cand.heading.lower()), "")):
                # Replace with the richer version
                for s in sections:
                    if s["heading"].lower() == cand.heading.lower():
                        s["content"] = content
                        s["page"] = cand.page
            continue

        sections.append(
            SectionEntry(heading=cand.heading, page=cand.page, content=content)
        )

    logger.debug("Detected %d sections", len(sections))
    return sections


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_heading(line: str) -> bool:
    """Return True when *line* looks like a section heading."""
    if not line:
        return False

    # Reject blocklisted phrases (manuscript notices, submission status)
    if _HEADING_BLOCKLIST_RE.match(line):
        return False

    # Reject very long lines — unlikely to be headings
    words = line.split()
    if len(words) > MAX_HEADING_WORDS:
        return False

    # Reject lines ending with sentence-terminating punctuation
    if line.endswith((".", ",", ";", ":", "?", "!")):
        stripped = _NUMERIC_PREFIX_RE.sub("", line).strip()
        if stripped.endswith((".", ",")):
            return False

    lower = line.lower()

    # Direct match against known academic section names
    if lower in KNOWN_HEADINGS:
        return True

    # Numeric prefix ("1 Introduction", "2.3 Methodology", "A. Appendix")
    if _NUMERIC_PREFIX_RE.match(line):
        return True

    # ALL CAPS heading (at least 4 alpha chars, no digits/symbols dominating)
    if line.isupper() and len(line) >= 4:
        # Reject if line contains digits or currency symbols — likely a value not a heading
        alpha_chars = sum(1 for c in line if c.isalpha())
        if alpha_chars < 4:
            return False
        if any(c in line for c in ('$', '%', '€', '£', '(', ')')):
            return False
        return True

    # Title Case — DISABLED: too noisy, catches paper titles and author names.
    # Known headings and numeric prefixes cover all real cases we need.
    # if _is_title_case(words) and len(words) >= 2:
    #     return True

    return False

def _is_title_case(words: list[str]) -> bool:
    """Return True when every word in *words* starts with an uppercase letter."""
    if not words:
        return False
    return all(w and w[0].isupper() for w in words)

