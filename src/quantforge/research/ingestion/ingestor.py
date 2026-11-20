# src/quantforge/research/ingestion/ingestor.py

"""
User-supplied PDF ingestion.

Accepts a local PDF file and injects it into the pipeline at ACQUIRED state,
bypassing discovery, normalization, scoring, and acquisition.

The paper enters at ACQUIRED — identical to a paper that completed the
full acquisition pipeline. Everything from processing onward is unchanged.
"""

from __future__ import annotations

import shutil
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from quantforge.database.models.paper import PaperRecord
from quantforge.database.repositories.paper import PaperRepository
from quantforge.models.states import PaperState
from quantforge.research.acquisition.validator import validate_pdf

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    """Result of a user-supplied PDF ingestion."""
    success: bool
    paper_id: UUID | None = None
    file_path: Path | None = None
    error: str | None = None


def ingest_pdf(
    source_file: Path,
    title: str,
    repo: PaperRepository,
    library_root: Path,
    source_name: str = "user_supplied",
    source_id: str | None = None,
    source_url: str | None = None,
    abstract: str | None = None,
    max_pdf_size_mb: int = 50,
) -> IngestResult:
    """
    Validate a user-supplied PDF, copy it to the research library,
    and create a PaperRecord at ACQUIRED state.

    Args:
        source_file: Path to the user's PDF file.
        title: Paper title (required — no discovery to get it from).
        repo: PaperRepository for DB persistence.
        library_root: Root of the research library directory.
        source_name: Source identifier e.g. "ssrn", "user_supplied".
        source_id: Source-specific ID e.g. SSRN abstract ID.
        source_url: URL to the paper's abstract or landing page.
        abstract: Optional abstract text.
        max_pdf_size_mb: Maximum accepted PDF size.

    Returns:
        IngestResult with paper_id and file_path on success.
    """
    # --- Validate source file exists ---
    if not source_file.exists():
        return IngestResult(
            success=False,
            error=f"File not found: {source_file}",
        )

    if not source_file.is_file():
        return IngestResult(
            success=False,
            error=f"Not a file: {source_file}",
        )

    # --- Validate PDF ---
    validation = validate_pdf(source_file, max_pdf_size_mb)
    if not validation.valid:
        return IngestResult(
            success=False,
            error=f"PDF validation failed: {validation.failure_reason}",
        )

    # --- Create destination path ---
    paper_id = uuid4()
    dest_dir = library_root / str(paper_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / source_file.name

    # --- Copy file atomically ---
    try:
        tmp_path = dest_path.with_suffix(".tmp")
        shutil.copy2(source_file, tmp_path)
        tmp_path.replace(dest_path)
        logger.info(
            "Ingested PDF: %s → %s (%d bytes)",
            source_file.name, dest_path, validation.size_bytes,
        )
    except Exception as e:
        # Clean up if copy failed
        if dest_dir.exists() and not any(dest_dir.iterdir()):
            dest_dir.rmdir()
        return IngestResult(
            success=False,
            error=f"Failed to copy file: {e}",
        )

    # --- Build source reference ---
    source_ref = {
        "source_name": source_name,
        "source_id": source_id or str(paper_id),
        "source_url": source_url,
        "raw_metadata": {
            "ingested_at": datetime.now(timezone.utc).isoformat(),
            "original_filename": source_file.name,
            "user_supplied": True,
        },
    }

    # --- Create PaperRecord at ACQUIRED state ---
    try:
        record = PaperRecord(
            id=paper_id,
            title=title,
            abstract=abstract,
            authors=[],
            keywords=[],
            sources=[source_ref],
            state=PaperState.ACQUIRED.value,
            file_path=str(dest_path),
            file_hash=validation.sha256,
            file_size_bytes=validation.size_bytes,
            evaluation={
                "acquisition": {
                    "source_url": source_url,
                    "resolver_source": source_name,
                    "acquired_at": datetime.now(timezone.utc).isoformat(),
                    "sha256": validation.sha256,
                    "size_bytes": validation.size_bytes,
                    "user_supplied": True,
                }
            },
        )
        repo.create(record)
        logger.info(
            "Created PaperRecord id=%s state=ACQUIRED title=%r",
            paper_id, title[:60],
        )
    except Exception as e:
        # Clean up file if DB write failed
        dest_path.unlink(missing_ok=True)
        if dest_dir.exists() and not any(dest_dir.iterdir()):
            dest_dir.rmdir()
        return IngestResult(
            success=False,
            error=f"Failed to create DB record: {e}",
        )

    return IngestResult(
        success=True,
        paper_id=paper_id,
        file_path=dest_path,
    )