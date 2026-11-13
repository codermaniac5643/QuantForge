# src/quantforge/research/processing/extractor.py
"""
PDF text extraction using PyMuPDF (fitz).

Responsibilities:
  - Extract full text from a PDF, page by page, preserving page boundaries.
  - Detect extraction failures (suspiciously empty or zero-character output).
  - Return structured ExtractionResult with per-page text and metadata.

What it does NOT do:
  - Parse or interpret section headings (sections.py).
  - Extract tables (pdfplumber — handled in packager if present).
  - Write any files to disk (processor.py / packager.py).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

# A PDF is considered to have failed extraction when fewer than this many
# non-whitespace characters are found across all pages.
MIN_CHARS_THRESHOLD = 50

PROCESSING_VERSION = "1.0.0"


@dataclass
class PageText:
    """Text content extracted from a single PDF page."""

    page_number: int  # 1-based
    text: str


@dataclass
class ExtractionResult:
    """
    Result of PDF text extraction.

    Attributes:
        pages:              Per-page text, in document order (1-based page_number).
        full_text:          Concatenated text across all pages, separated by
                            page-boundary markers.
        page_count:         Total number of pages in the PDF.
        char_count:         Total non-whitespace character count.
        extraction_failed:  True when output is suspiciously empty.
        failure_reason:     Human-readable explanation when extraction_failed is True.
        processing_version: Version string for this extractor.
    """

    pages: list[PageText] = field(default_factory=list)
    full_text: str = ""
    page_count: int = 0
    char_count: int = 0
    extraction_failed: bool = False
    failure_reason: str | None = None
    processing_version: str = PROCESSING_VERSION


def extract_text(pdf_path: Path) -> ExtractionResult:
    """
    Extract text from *pdf_path* using PyMuPDF.

    Page boundaries are marked as:
        --- Page N ---

    Returns an ExtractionResult.  Never raises — extraction failures are
    captured in ExtractionResult.extraction_failed / failure_reason.

    Args:
        pdf_path: Absolute path to a valid PDF file.

    Returns:
        ExtractionResult with per-page text and aggregate metadata.
    """
    pdf_path = Path(pdf_path)

    if not pdf_path.exists():
        return ExtractionResult(
            extraction_failed=True,
            failure_reason=f"File not found: {pdf_path}",
        )

    if not pdf_path.is_file():
        return ExtractionResult(
            extraction_failed=True,
            failure_reason=f"Path is not a file: {pdf_path}",
        )

    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:  # fitz.FileDataError, etc.
        logger.warning("PyMuPDF failed to open %s: %s", pdf_path, exc)
        return ExtractionResult(
            extraction_failed=True,
            failure_reason=f"Could not open PDF: {exc}",
        )

    page_count = doc.page_count
    pages: list[PageText] = []
    text_parts: list[str] = []

    try:
        for page_index in range(page_count):
            page = doc[page_index]
            page_text = page.get_text("text")  # plain text, preserves layout words
            page_number = page_index + 1
            pages.append(PageText(page_number=page_number, text=page_text))
            text_parts.append(f"--- Page {page_number} ---\n{page_text}")
    finally:
        doc.close()

    full_text = "\n".join(text_parts)
    char_count = len(full_text.replace(" ", "").replace("\n", "").replace("\t", ""))

    failed, reason = detect_extraction_failure(full_text, page_count)

    result = ExtractionResult(
        pages=pages,
        full_text=full_text,
        page_count=page_count,
        char_count=char_count,
        extraction_failed=failed,
        failure_reason=reason,
        processing_version=PROCESSING_VERSION,
    )

    if failed:
        logger.warning("Extraction failure for %s: %s", pdf_path.name, reason)
    else:
        logger.debug(
            "Extracted %d chars from %d pages of %s",
            char_count, page_count, pdf_path.name,
        )

    return result


def detect_extraction_failure(text: str, page_count: int) -> tuple[bool, str | None]:
    """
    Heuristically decide whether an extracted text looks valid.

    Rules:
      1. Zero pages → failed.
      2. Fewer than MIN_CHARS_THRESHOLD non-whitespace characters → failed.

    Args:
        text:       Full extracted text string.
        page_count: Number of pages the PDF reported.

    Returns:
        (failed: bool, reason: str | None)
    """
    if page_count == 0:
        return True, "PDF has zero pages"

    non_ws = len(text.replace(" ", "").replace("\n", "").replace("\t", ""))
    if non_ws < MIN_CHARS_THRESHOLD:
        return True, (
            f"Extracted only {non_ws} non-whitespace characters "
            f"from {page_count} page(s) — likely a scanned or encrypted PDF"
        )

    return False, None

