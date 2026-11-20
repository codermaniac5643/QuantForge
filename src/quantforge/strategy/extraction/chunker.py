# src/quantforge/strategy/extraction/chunker.py

"""
Text chunking utilities for the strategy extraction pipeline.

Two responsibilities:
1. Section size guard — split sections that exceed the per-section token budget.
2. Full-text fallback — chunk text.txt when sections.json has no usable headings.

Token estimation uses a 4-chars-per-token heuristic — accurate enough for
budget control without requiring a tokenizer dependency.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
import re as _re

logger = logging.getLogger(__name__)

# 4 characters ≈ 1 token (GPT/Claude family heuristic)
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Estimate token count from character count."""
    return max(1, len(text) // CHARS_PER_TOKEN)


# ---------------------------------------------------------------------------
# Formula quality constants  (must be defined before Section uses it)
# ---------------------------------------------------------------------------


class FormulaQuality(str):
    CLEAN = "clean"
    DEGRADED = "degraded"
    MISSING = "missing"


# ---------------------------------------------------------------------------
# Section data structure
# ---------------------------------------------------------------------------


@dataclass
class Section:
    """A single section from sections.json or a text chunk."""
    index: int
    heading: str
    content: str
    formula_quality: str = FormulaQuality.MISSING  # set after detection

    @property
    def estimated_tokens(self) -> int:
        return estimate_tokens(self.content)

    @property
    def content_length(self) -> int:
        return len(self.content)


# ---------------------------------------------------------------------------
# Section quality filter (STRATEGY_SCHEMA.md Section 9.2)
# ---------------------------------------------------------------------------


def filter_sections(
    sections: list[Section],
    min_chars: int = 200,
) -> tuple[list[Section], list[int]]:
    """
    Filter out sections below the minimum character threshold.

    Args:
        sections: All sections from sections.json.
        min_chars: Minimum content length to keep (default 200).

    Returns:
        Tuple of (kept_sections, filtered_indices).
        filtered_indices goes into extraction_audit.sections_filtered_too_short.
    """
    kept: list[Section] = []
    filtered_indices: list[int] = []

    for section in sections:
        if section.content_length >= min_chars:
            kept.append(section)
        else:
            filtered_indices.append(section.index)
            logger.debug(
                "Section [%d] %r filtered — %d chars < min %d",
                section.index, section.heading,
                section.content_length, min_chars,
            )

    if filtered_indices:
        logger.info(
            "Section quality filter: kept %d, filtered %d (too short)",
            len(kept), len(filtered_indices),
        )

    return kept, filtered_indices


# ---------------------------------------------------------------------------
# Section size guard
# ---------------------------------------------------------------------------


def split_section(
    section: Section,
    max_tokens: int = 1500,
    overlap_chars: int = 200,
) -> list[Section]:
    """
    Split a section that exceeds the token budget into overlapping chunks.

    Each chunk gets a synthetic heading with a part number.
    The original section index is preserved — chunks share the same index.

    Args:
        section: Section to split.
        max_tokens: Maximum tokens per chunk.
        overlap_chars: Character overlap between consecutive chunks.

    Returns:
        List of Section chunks. Returns [section] unchanged if within budget.
    """
    if section.estimated_tokens <= max_tokens:
        return [section]

    max_chars = max_tokens * CHARS_PER_TOKEN
    content = section.content
    chunks: list[Section] = []
    start = 0
    part = 1

    while start < len(content):
        end = start + max_chars

        # Try to break at a paragraph boundary
        if end < len(content):
            boundary = content.rfind("\n\n", start, end)
            if boundary == -1:
                boundary = content.rfind("\n", start, end)
            if boundary != -1 and boundary > start:
                end = boundary

        chunk_content = content[start:end].strip()
        if chunk_content:
            chunks.append(Section(
                index=section.index,
                heading=f"{section.heading} (part {part})",
                content=chunk_content,
            ))
            part += 1

        # Advance with overlap
        start = end - overlap_chars
        if start <= 0:
            break

    logger.info(
        "Section [%d] %r split into %d chunks (%d tokens → max %d per chunk)",
        section.index, section.heading,
        len(chunks), section.estimated_tokens, max_tokens,
    )
    return chunks


def apply_size_guard(
    sections: list[Section],
    max_tokens: int = 1500,
    overlap_chars: int = 200,
) -> list[Section]:
    """
    Apply the section size guard to all sections.
    Returns a flat list with oversized sections split into chunks.
    """
    result: list[Section] = []
    for section in sections:
        result.extend(split_section(section, max_tokens, overlap_chars))
    return result


# ---------------------------------------------------------------------------
# Full-text fallback chunker
# ---------------------------------------------------------------------------


def chunk_full_text(
    text: str,
    chunk_size_chars: int = 6000,
    overlap_chars: int = 200,
) -> list[Section]:
    """
    Chunk raw text.txt content into overlapping sections.
    Used as a fallback when sections.json has no usable headings.

    Each chunk is assigned a synthetic index and heading.
    Chunks attempt to break at paragraph boundaries.

    Args:
        text: Full text content from text.txt.
        chunk_size_chars: Target chunk size in characters.
        overlap_chars: Character overlap between consecutive chunks.

    Returns:
        List of Section objects with synthetic headings.
    """
    if not text.strip():
        logger.warning("Full-text fallback received empty text")
        return []

    chunks: list[Section] = []
    start = 0
    index = 0

    while start < len(text):
        end = start + chunk_size_chars

        # Try to break at a paragraph boundary
        if end < len(text):
            boundary = text.rfind("\n\n", start, end)
            if boundary == -1:
                boundary = text.rfind("\n", start, end)
            if boundary != -1 and boundary > start:
                end = boundary

        chunk_content = text[start:end].strip()
        if chunk_content:
            chunks.append(Section(
                index=index,
                heading=f"Text Chunk {index + 1}",
                content=chunk_content,
            ))
            index += 1

        start = end - overlap_chars
        if start <= 0:
            break

    logger.warning(
        "Full-text fallback: produced %d chunks from %d chars",
        len(chunks), len(text),
    )
    return chunks


def load_sections_from_package(
    sections_data: list[dict],
) -> list[Section]:
    """
    Convert sections.json data into Section objects.
    Runs formula quality detection on each section.
    """
    sections: list[Section] = []
    for i, entry in enumerate(sections_data):
        heading = entry.get("heading", f"Section {i}")
        content = entry.get("content", "")
        sections.append(Section(
            index=i,
            heading=heading,
            content=content,
            formula_quality=detect_formula_quality(content),
        ))
    return sections

# ---------------------------------------------------------------------------
# Formula quality detection
# ---------------------------------------------------------------------------
_FORMULA_SIGNALS = _re.compile(
    r'[\^_{}\\]'               # LaTeX remnants: ^, _, {}, \
    r'|[∑∏∫√≤≥≠±×÷αβγδεζηθλμνπρσφωΔΩ]'  # Greek / math symbols
    r'|\b[A-Z][a-z]?\s*[=<>]\s*[-\d]'    # variable assignments: R = 0.5
    r'|\d+\s*[+\-*/]\s*\d+'              # arithmetic expressions
    r'|\([^)]{1,30}\)\s*[=<>]'           # parenthetical expressions
    r'|E\[|Var\[|Cov\['                   # statistics notation
    r'|\|[^|]+\|'                         # absolute value notation
)

# Signals of degraded/garbled formulas
_DEGRADED_SIGNALS = _re.compile(
    r'\\[a-zA-Z]{2,}'          # unrendered LaTeX commands: \frac, \alpha
    r'|[□■▪◦•]{2,}'            # replacement characters
    r'|\?\?+'                   # multiple question marks (encoding failure)
    r'|[^\x00-\x7F]{3,}'       # 3+ consecutive non-ASCII (encoding issues)
    r'|\s[a-z]\s=\s\?'         # variable = ? (missing value)
)


def detect_formula_quality(content: str) -> str:
    """
    Detect the quality of formula content in a section.

    Returns:
        "clean"    — formulas present and appear intact
        "degraded" — formulas present but show signs of garbling
        "missing"  — no formula-like content detected
    """
    if not content.strip():
        return FormulaQuality.MISSING

    has_formulas = bool(_FORMULA_SIGNALS.search(content))
    has_degraded = bool(_DEGRADED_SIGNALS.search(content))

    if not has_formulas:
        return FormulaQuality.MISSING
    if has_degraded:
        return FormulaQuality.DEGRADED
    return FormulaQuality.CLEAN