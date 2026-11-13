# src/quantforge/research/processing/packager.py
"""
Research Document Package creation.

Responsibilities:
  - Assemble all processing artefacts for one paper into a structured
    directory under research_library/<paper_id>/.
  - Write metadata.json, text.txt, sections.json, and acquisition.json.
  - Optionally attempt table extraction with pdfplumber and write
    tables/<n>.json when rows are found.
  - Idempotent: safe to rerun — existing files are overwritten atomically.
  - Record a package_manifest.json describing which files were written and
    their sizes, plus processing version and timestamps.

Package layout:
    research_library/<paper_id>/
        metadata.json        ← bibliographic fields from DB record
        paper.pdf            ← already on disk from acquisition
        text.txt             ← full extracted text
        sections.json        ← list of SectionEntry dicts
        tables/              ← one file per table (optional, may be absent)
            table_001.json
        acquisition.json     ← acquisition provenance from evaluation JSON
        package_manifest.json← file inventory + processing metadata

What it does NOT do:
  - Run text extraction (extractor.py).
  - Detect section headings (sections.py).
  - Transition pipeline states (processor.py).
  - Write anything to PostgreSQL.
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pdfplumber

from quantforge.research.processing.extractor import ExtractionResult, PROCESSING_VERSION
from quantforge.research.processing.sections import SectionEntry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class PackageManifest:
    """
    Inventory of a completed Research Document Package.

    Attributes:
        paper_id:           UUID of the paper (as str).
        package_dir:        Absolute path of the package directory.
        files_written:      Mapping of filename → byte size for every written file.
        table_count:        Number of tables extracted (0 when none found).
        processing_version: Version string of this packager.
        packaged_at:        UTC timestamp when packaging completed.
        warnings:           Non-fatal notes recorded during packaging.
    """

    paper_id: str
    package_dir: Path
    files_written: dict[str, int] = field(default_factory=dict)
    table_count: int = 0
    processing_version: str = PROCESSING_VERSION
    packaged_at: str = ""
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_research_package(
    *,
    paper_id: str,
    package_dir: Path,
    record_dict: dict[str, Any],
    extraction: ExtractionResult,
    sections: list[SectionEntry],
    extract_tables: bool = True,
) -> PackageManifest:
    """
    Write a Research Document Package for one paper.

    Args:
        paper_id:       UUID of the paper (string form).
        package_dir:    Root directory for this paper's package.  Will be
                        created if it does not exist.
        record_dict:    Dict of bibliographic / acquisition fields from the
                        DB record (serialisable with json.dumps).
        extraction:     ExtractionResult produced by extractor.extract_text().
        sections:       List of SectionEntry dicts from sections.extract_sections().
        extract_tables: Whether to run pdfplumber table extraction.  Disable
                        in tests to avoid needing a real PDF.

    Returns:
        PackageManifest describing every file written.
    """
    package_dir = Path(package_dir)
    package_dir.mkdir(parents=True, exist_ok=True)

    manifest = PackageManifest(paper_id=paper_id, package_dir=package_dir)
    warnings: list[str] = []

    # --- metadata.json ---
    _write_json_atomic(
        package_dir / "metadata.json",
        _build_metadata(record_dict, extraction),
        manifest,
    )

    # --- text.txt ---
    _write_text_atomic(
        package_dir / "text.txt",
        extraction.full_text,
        manifest,
    )

    # --- sections.json ---
    _write_json_atomic(
        package_dir / "sections.json",
        sections,  # list of TypedDict — JSON-serialisable
        manifest,
    )

    # --- acquisition.json ---
    acq_data = record_dict.get("evaluation") or {}
    acq_section = acq_data.get("acquisition") if isinstance(acq_data, dict) else {}
    _write_json_atomic(
        package_dir / "acquisition.json",
        acq_section or {},
        manifest,
    )

    # --- tables/ (pdfplumber, optional) ---
    if extract_tables:
        pdf_path_str = record_dict.get("file_path")
        if pdf_path_str and Path(pdf_path_str).exists():
            table_count, table_warnings = _extract_and_write_tables(
                pdf_path=Path(pdf_path_str),
                tables_dir=package_dir / "tables",
                manifest=manifest,
            )
            manifest.table_count = table_count
            warnings.extend(table_warnings)
        else:
            warnings.append("file_path missing or file absent — skipping table extraction")

    # --- package_manifest.json ---
    manifest.packaged_at = datetime.now(timezone.utc).isoformat()
    manifest.warnings = warnings
    _write_json_atomic(
        package_dir / "package_manifest.json",
        _manifest_to_dict(manifest),
        manifest,
    )

    logger.info(
        "Package written for paper %s: %d files, %d tables",
        paper_id,
        len(manifest.files_written),
        manifest.table_count,
    )
    return manifest


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_metadata(record_dict: dict[str, Any], extraction: ExtractionResult) -> dict[str, Any]:
    """Combine DB record fields with extraction metadata."""
    return {
        "paper_id": record_dict.get("id"),
        "title": record_dict.get("title"),
        "doi": record_dict.get("doi"),
        "arxiv_id": record_dict.get("arxiv_id"),
        "authors": record_dict.get("authors", []),
        "abstract": record_dict.get("abstract"),
        "published_date": record_dict.get("published_date"),
        "journal": record_dict.get("journal"),
        "keywords": record_dict.get("keywords", []),
        "sources": record_dict.get("sources", []),
        "state": record_dict.get("state"),
        "relevance_score": record_dict.get("relevance_score"),
        "credibility_score": record_dict.get("credibility_score"),
        "implementability_score": record_dict.get("implementability_score"),
        "reproducibility_score": record_dict.get("reproducibility_score"),
        "composite_score": record_dict.get("composite_score"),
        "file_hash": record_dict.get("file_hash"),
        "file_size_bytes": record_dict.get("file_size_bytes"),
        "extraction": {
            "page_count": extraction.page_count,
            "char_count": extraction.char_count,
            "extraction_failed": extraction.extraction_failed,
            "failure_reason": extraction.failure_reason,
            "processing_version": extraction.processing_version,
        },
    }


def _write_json_atomic(path: Path, data: Any, manifest: PackageManifest) -> None:
    """Write *data* as JSON to *path* atomically (temp → rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with open(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, default=str, ensure_ascii=False)
        shutil.move(tmp_path, path)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise
    manifest.files_written[path.name] = path.stat().st_size


def _write_text_atomic(path: Path, text: str, manifest: PackageManifest) -> None:
    """Write *text* to *path* atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with open(tmp_fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        shutil.move(tmp_path, path)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise
    manifest.files_written[path.name] = path.stat().st_size


def _extract_and_write_tables(
    pdf_path: Path,
    tables_dir: Path,
    manifest: PackageManifest,
) -> tuple[int, list[str]]:
    """
    Use pdfplumber to extract tables from *pdf_path*.

    Each table is written as tables/table_NNN.json containing
    {"page": int, "rows": list[list[str|None]]}.

    Returns (table_count, warnings).
    """
    warnings: list[str] = []
    table_count = 0

    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages:
                try:
                    tables = page.extract_tables()
                except Exception as exc:
                    warnings.append(
                        f"pdfplumber failed on page {page.page_number}: {exc}"
                    )
                    continue

                for table in tables:
                    if not table:
                        continue
                    table_count += 1
                    out_path = tables_dir / f"table_{table_count:03d}.json"
                    _write_json_atomic(
                        out_path,
                        {"page": page.page_number, "rows": table},
                        manifest,
                    )
                    manifest.files_written[f"tables/table_{table_count:03d}.json"] = (
                        out_path.stat().st_size
                    )
    except Exception as exc:
        warnings.append(f"pdfplumber could not open PDF: {exc}")

    return table_count, warnings


def _manifest_to_dict(manifest: PackageManifest) -> dict[str, Any]:
    """Serialise PackageManifest to a plain dict."""
    return {
        "paper_id": manifest.paper_id,
        "package_dir": str(manifest.package_dir),
        "files_written": manifest.files_written,
        "table_count": manifest.table_count,
        "processing_version": manifest.processing_version,
        "packaged_at": manifest.packaged_at,
        "warnings": manifest.warnings,
    }

