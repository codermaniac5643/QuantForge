# src/quantforge/research/processing/processor.py
"""
PdfProcessor — orchestrates the full processing pipeline for one paper.

Responsibilities:
  - Read file_path from the DB record.
  - Run text extraction (extractor.py).
  - Run section detection (sections.py).
  - Build the Research Document Package (packager.py).
  - Transition state ACQUIRED → PROCESSED via PaperRepository.
  - Record processing version, timestamps, and extraction metadata in
    evaluation JSON.
  - Return a ProcessingResult — never raise on paper-level errors.

What it does NOT do:
  - Download PDFs (acquisition/manager.py).
  - Score or rank papers (filtering/scorer.py).
  - Call the LLM (Milestone 6).
  - Write CLI output (Milestone 10).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from quantforge.database.connection import get_session
from quantforge.database.repositories.paper import PaperRepository, InvalidStateTransitionError
from quantforge.models.states import PaperState
from quantforge.research.processing.extractor import extract_text, ExtractionResult, PROCESSING_VERSION
from quantforge.research.processing.sections import extract_sections
from quantforge.research.processing.packager import create_research_package, PackageManifest

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result data class
# ---------------------------------------------------------------------------


@dataclass
class ProcessingResult:
    """
    Outcome of processing one paper.

    Attributes:
        paper_id:       UUID of the paper.
        success:        True when extraction succeeded and package was written.
        final_state:    PaperState the paper is in after this attempt.
        package_dir:    Path of the Research Document Package directory.
        manifest:       PackageManifest from packager (None on hard failure).
        extraction:     ExtractionResult (None on hard failure before extraction).
        section_count:  Number of sections detected (0 on failure).
        error:          Human-readable error description (None on success).
    """

    paper_id: UUID
    success: bool
    final_state: PaperState
    package_dir: Path | None = None
    manifest: PackageManifest | None = None
    extraction: ExtractionResult | None = None
    section_count: int = 0
    error: str | None = None


# ---------------------------------------------------------------------------
# Processor
# ---------------------------------------------------------------------------


class PdfProcessor:
    """
    Processes an acquired paper PDF into a Research Document Package.

    Usage::

        processor = PdfProcessor(storage_root=Path("research_library"))
        result = processor.process(paper_id=uuid, repo=repo)
    """

    def __init__(self, storage_root: Path) -> None:
        """
        Args:
            storage_root: Root directory for research_library.
                          Package will be written to storage_root/<paper_id>/.
        """
        self.storage_root = Path(storage_root)

    def process(self, paper_id: UUID, repo: PaperRepository) -> ProcessingResult:
        """
        Run the full processing pipeline for *paper_id*.

        Steps:
          1. Fetch DB record — must exist and be in ACQUIRED state.
          2. Locate the PDF file on disk.
          3. Extract text with PyMuPDF.
          4. Detect sections.
          5. Write the Research Document Package.
          6. Update evaluation JSON with processing metadata.
          7. Transition state ACQUIRED → PROCESSED.

        A processing failure (bad PDF, missing file, extraction failure)
        does NOT transition the paper to REJECTED.  The paper stays in
        ACQUIRED so it can be retried.  Only state-transition errors are
        escalated.

        Args:
            paper_id: UUID of the paper to process.
            repo:     Open PaperRepository bound to an active session.

        Returns:
            ProcessingResult.
        """
        started_at = datetime.now(timezone.utc).isoformat()

        # --- 1. Load DB record ---
        record = repo.get_by_id(paper_id)
        if record is None:
            return ProcessingResult(
                paper_id=paper_id,
                success=False,
                final_state=PaperState.ACQUIRED,  # unknown — keep safe default
                error=f"Paper {paper_id} not found in database",
            )

        current_state = PaperState(record.state)

        if current_state == PaperState.PROCESSED:
            logger.info("Paper %s already PROCESSED — re-running to refresh package", paper_id)

        elif current_state != PaperState.ACQUIRED:
            return ProcessingResult(
                paper_id=paper_id,
                success=False,
                final_state=current_state,
                error=(
                    f"Paper {paper_id} is in state {current_state.value!r} "
                    f"— expected ACQUIRED"
                ),
            )

        # --- 2. Locate PDF ---
        if not record.file_path:
            return ProcessingResult(
                paper_id=paper_id,
                success=False,
                final_state=current_state,
                error=f"Paper {paper_id} has no file_path recorded",
            )

        pdf_path = Path(record.file_path)
        if not pdf_path.exists():
            return ProcessingResult(
                paper_id=paper_id,
                success=False,
                final_state=current_state,
                error=f"PDF not found on disk: {pdf_path}",
            )

        # --- 3. Extract text ---
        logger.info("Extracting text from %s", pdf_path.name)
        extraction = extract_text(pdf_path)

        if extraction.extraction_failed:
            # Non-fatal — still write whatever we have; warn in manifest
            logger.warning(
                "Extraction failure for paper %s: %s",
                paper_id,
                extraction.failure_reason,
            )

        # --- 4. Detect sections ---
        sections = extract_sections(extraction.full_text)
        logger.debug("Detected %d sections for paper %s", len(sections), paper_id)

        # --- 5. Build package directory ---
        package_dir = self.storage_root / str(paper_id)

        record_dict = _record_to_dict(record)
        manifest = create_research_package(
            paper_id=str(paper_id),
            package_dir=package_dir,
            record_dict=record_dict,
            extraction=extraction,
            sections=sections,
            extract_tables=True,
        )

        # --- 6. Update evaluation JSON ---
        finished_at = datetime.now(timezone.utc).isoformat()
        processing_meta = {
            "processing_version": PROCESSING_VERSION,
            "started_at": started_at,
            "finished_at": finished_at,
            "page_count": extraction.page_count,
            "char_count": extraction.char_count,
            "section_count": len(sections),
            "table_count": manifest.table_count,
            "extraction_failed": extraction.extraction_failed,
            "failure_reason": extraction.failure_reason,
            "package_dir": str(package_dir),
        }
        existing_eval: dict = record.evaluation or {}
        existing_eval["processing"] = processing_meta
        record.evaluation = existing_eval
        repo.session.flush()

        # --- 7. Transition state (ACQUIRED → PROCESSED or re-mark PROCESSED) ---
        if current_state == PaperState.ACQUIRED:
            try:
                repo.transition_state(paper_id, PaperState.PROCESSED)
            except InvalidStateTransitionError as exc:
                return ProcessingResult(
                    paper_id=paper_id,
                    success=False,
                    final_state=current_state,
                    package_dir=package_dir,
                    manifest=manifest,
                    extraction=extraction,
                    section_count=len(sections),
                    error=f"State transition failed: {exc}",
                )

        final_state = PaperState.PROCESSED
        logger.info(
            "Paper %s processed: %d pages, %d sections, %d tables → PROCESSED",
            paper_id,
            extraction.page_count,
            len(sections),
            manifest.table_count,
        )

        return ProcessingResult(
            paper_id=paper_id,
            success=True,
            final_state=final_state,
            package_dir=package_dir,
            manifest=manifest,
            extraction=extraction,
            section_count=len(sections),
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _record_to_dict(record) -> dict:
    """Convert a PaperRecord ORM object to a plain JSON-serialisable dict."""
    return {
        "id": str(record.id),
        "doi": record.doi,
        "arxiv_id": record.arxiv_id,
        "title": record.title,
        "abstract": record.abstract,
        "published_date": (
            record.published_date.isoformat() if record.published_date else None
        ),
        "journal": record.journal,
        "authors": record.authors or [],
        "keywords": record.keywords or [],
        "sources": record.sources or [],
        "state": record.state if isinstance(record.state, str) else record.state.value,
        "relevance_score": record.relevance_score,
        "credibility_score": record.credibility_score,
        "implementability_score": record.implementability_score,
        "reproducibility_score": record.reproducibility_score,
        "composite_score": record.composite_score,
        "file_path": record.file_path,
        "file_hash": record.file_hash,
        "file_size_bytes": record.file_size_bytes,
        "evaluation": record.evaluation or {},
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }

