# src/quantforge/research/acquisition/manager.py
"""
AcquisitionManager — orchestrates paper download for QuantForge.

Responsibilities:
  - Enforce weekly and monthly download caps (checked against the DB).
  - Respect approval_mode: "manual" (APPROVED state required) or
    "auto" (CANDIDATE papers above composite threshold are auto-approved).
  - Resolve the best PDF URL via PdfResolver.
  - Stream the file to a temp path, validate, then place atomically.
  - Record provenance (source_url, acquired_at, hash, size) on PaperRecord.
  - Transition state via PaperRepository.transition_state() — never directly.
  - Return an AcquisitionOutcome per paper; never raise on paper-level errors.

What it does NOT do:
  - Parse PDF content (Milestone 8).
  - Score or rank papers (Milestone 5).
  - Call the LLM (Milestone 6).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

import httpx

from quantforge.config.loader import AcquisitionConfig
from quantforge.database.models.paper import PaperRecord
from quantforge.database.repositories.paper import (
    PaperRepository,
    InvalidStateTransitionError,
)
from quantforge.models.states import PaperState
from quantforge.research.acquisition.downloader import download_pdf
from quantforge.research.acquisition.resolver import PdfResolver, ResolvedUrl
from quantforge.research.acquisition.validator import validate_pdf

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Outcome data classes
# ---------------------------------------------------------------------------

@dataclass
class AcquisitionOutcome:
    """
    Result of one paper acquisition attempt.

    Attributes:
        paper_id:       UUID of the paper.
        success:        True when file is downloaded, validated, and stored.
        final_state:    PaperState the paper is in after this attempt.
        file_path:      Absolute path of the stored file (success only).
        sha256:         SHA-256 hash of the stored file (success only).
        size_bytes:     File size in bytes (success only).
        source_url:     URL the PDF was fetched from.
        failure_reason: Human-readable explanation when success=False.
    """
    paper_id: UUID
    success: bool
    final_state: PaperState
    file_path: Path | None = None
    sha256: str | None = None
    size_bytes: int = 0
    source_url: str | None = None
    failure_reason: str | None = None


@dataclass
class AcquisitionSummary:
    """
    Summary of a batch acquisition run.
    """
    acquired: list[AcquisitionOutcome] = field(default_factory=list)
    skipped: list[AcquisitionOutcome] = field(default_factory=list)
    failed: list[AcquisitionOutcome] = field(default_factory=list)

    @property
    def n_acquired(self) -> int:
        return len(self.acquired)

    @property
    def n_skipped(self) -> int:
        return len(self.skipped)

    @property
    def n_failed(self) -> int:
        return len(self.failed)


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class AcquisitionManager:
    """
    Orchestrates paper acquisition for QuantForge.

    Args:
        repo:        PaperRepository (session owned by caller).
        config:      AcquisitionConfig loaded from research_pipeline.yaml.
        storage_root: Base directory for the research library.
                      Defaults to Path(config.storage_path).
        client:      Optional injected httpx.AsyncClient.
                     A new client is created if not provided.
        mailto:      Email for Unpaywall polite pool.
        timeout:     Per-request HTTP timeout (seconds).
    """

    def __init__(
        self,
        repo: PaperRepository,
        config: AcquisitionConfig,
        storage_root: Path | None = None,
        client: httpx.AsyncClient | None = None,
        mailto: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._repo = repo
        self._config = config
        self._storage_root = storage_root or Path(config.storage_path)
        self._client = client
        self._mailto = mailto
        self._timeout = timeout
        self._owns_client = client is None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def acquire(
        self,
        paper_id: UUID,
        auto_approve_threshold: float = 0.5,
    ) -> AcquisitionOutcome:
        """
        Attempt to acquire a single paper.

        Args:
            paper_id:               UUID of the PaperRecord to acquire.
            auto_approve_threshold: Minimum composite score for auto-approval
                                    (only used when approval_mode="auto").

        Returns:
            AcquisitionOutcome describing the result.
        """
        record = self._repo.get_by_id(paper_id)
        if record is None:
            return AcquisitionOutcome(
                paper_id=paper_id,
                success=False,
                final_state=PaperState.REJECTED,
                failure_reason=f"Paper {paper_id} not found in database",
            )

        current_state = PaperState(record.state)

        # --- Approval gate ---
        outcome = await self._ensure_approved(record, current_state, auto_approve_threshold)
        if outcome is not None:
            return outcome

        # Refresh state after possible auto-approval
        record = self._repo.get_by_id(paper_id)
        assert record is not None

        # --- Already acquired guard ---
        if record.file_path and record.file_hash:
            logger.info(
                "Paper %s already acquired at %s — skipping",
                paper_id, record.file_path,
            )
            return AcquisitionOutcome(
                paper_id=paper_id,
                success=True,
                final_state=PaperState.ACQUIRED,
                file_path=Path(record.file_path),
                sha256=record.file_hash,
                size_bytes=record.file_size_bytes or 0,
                failure_reason="Already acquired — skipped duplicate download",
            )

        # --- Weekly/monthly cap ---
        cap_outcome = self._check_caps(paper_id)
        if cap_outcome is not None:
            return cap_outcome

        # --- Resolve PDF URL ---
        client = self._get_client()
        resolver = PdfResolver(client=client, mailto=self._mailto, timeout=self._timeout)

        from quantforge.models.paper import ResearchPaper, SourceReference, Author
        # Build a lightweight ResearchPaper from the DB record for the resolver
        paper_model = self._record_to_model(record)

        resolved: ResolvedUrl = await resolver.resolve(paper_model)

        if resolved.url is None:
            # Mark unavailable
            self._record_failure(record, resolved.failure_reason or "URL resolution failed")
            self._repo.transition_state(paper_id, PaperState.REJECTED)
            return AcquisitionOutcome(
                paper_id=paper_id,
                success=False,
                final_state=PaperState.REJECTED,
                failure_reason=resolved.failure_reason,
            )

        # --- Download ---
        dest_path = self._build_dest_path(record)
        max_bytes = self._config.max_pdf_size_mb * 1024 * 1024

        dl_result = await download_pdf(
            client=client,
            url=resolved.url,
            dest_path=dest_path,
            timeout=self._timeout,
            max_size_bytes=max_bytes,
        )

        if not dl_result.success:
            # Leave in APPROVED state so it can be retried
            self._record_failure(record, dl_result.failure_reason or "Download failed")
            logger.warning(
                "Download failed for paper %s: %s", paper_id, dl_result.failure_reason
            )
            return AcquisitionOutcome(
                paper_id=paper_id,
                success=False,
                final_state=PaperState.APPROVED,
                source_url=resolved.url,
                failure_reason=dl_result.failure_reason,
            )

        # --- Validate ---
        assert dl_result.dest_path is not None
        val_result = validate_pdf(dl_result.dest_path, self._config.max_pdf_size_mb)

        if not val_result.valid:
            dl_result.dest_path.unlink(missing_ok=True)
            self._record_failure(record, val_result.failure_reason or "Validation failed")
            self._repo.transition_state(paper_id, PaperState.REJECTED)
            return AcquisitionOutcome(
                paper_id=paper_id,
                success=False,
                final_state=PaperState.REJECTED,
                source_url=resolved.url,
                sha256=val_result.sha256,
                size_bytes=val_result.size_bytes,
                failure_reason=val_result.failure_reason,
            )

        # --- Persist provenance ---
        record.file_path = str(dl_result.dest_path)
        record.file_hash = val_result.sha256
        record.file_size_bytes = val_result.size_bytes
        # Store acquisition metadata in the evaluation JSON field (append)
        existing_eval: dict = record.evaluation or {}
        existing_eval["acquisition"] = {
            "source_url": resolved.url,
            "resolver_source": resolved.source,
            "acquired_at": datetime.now(timezone.utc).isoformat(),
            "sha256": val_result.sha256,
            "size_bytes": val_result.size_bytes,
        }
        record.evaluation = existing_eval
        self._repo.session.flush()

        # --- Transition to ACQUIRED ---
        self._repo.transition_state(paper_id, PaperState.ACQUIRED)

        logger.info(
            "Paper %s acquired → %s (%d bytes, sha256=%s…)",
            paper_id,
            dl_result.dest_path,
            val_result.size_bytes,
            (val_result.sha256 or "")[:12],
        )
        return AcquisitionOutcome(
            paper_id=paper_id,
            success=True,
            final_state=PaperState.ACQUIRED,
            file_path=dl_result.dest_path,
            sha256=val_result.sha256,
            size_bytes=val_result.size_bytes,
            source_url=resolved.url,
        )

    async def acquire_batch(
        self,
        paper_ids: list[UUID],
        auto_approve_threshold: float = 0.5,
    ) -> AcquisitionSummary:
        """
        Acquire a list of papers in sequence (not concurrent — cap safety).

        Returns an AcquisitionSummary grouping outcomes.
        """
        summary = AcquisitionSummary()
        for pid in paper_ids:
            outcome = await self.acquire(pid, auto_approve_threshold)
            if outcome.success:
                summary.acquired.append(outcome)
            elif outcome.failure_reason and "cap" in outcome.failure_reason.lower():
                summary.skipped.append(outcome)
            else:
                summary.failed.append(outcome)
        return summary

    async def close(self) -> None:
        """Release the HTTP client if we own it."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def __aenter__(self) -> "AcquisitionManager":
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient()
            self._owns_client = True
        return self._client

    async def _ensure_approved(
        self,
        record: PaperRecord,
        current_state: PaperState,
        threshold: float,
    ) -> AcquisitionOutcome | None:
        """
        Enforce the approval gate.

        Returns an AcquisitionOutcome if the paper cannot proceed, else None.
        """
        if current_state == PaperState.APPROVED:
            return None  # already approved — proceed

        if current_state == PaperState.ACQUIRED:
            return None  # already done — caller will detect and skip

        if self._config.approval_mode == "auto":
            if current_state != PaperState.CANDIDATE:
                return AcquisitionOutcome(
                    paper_id=record.id,
                    success=False,
                    final_state=current_state,
                    failure_reason=(
                        f"Auto-approval requires CANDIDATE state, got {current_state.value!r}"
                    ),
                )
            composite = record.composite_score or 0.0
            if composite < threshold:
                return AcquisitionOutcome(
                    paper_id=record.id,
                    success=False,
                    final_state=current_state,
                    failure_reason=(
                        f"Composite score {composite:.4f} below auto-approve "
                        f"threshold {threshold}"
                    ),
                )
            # Auto-approve
            try:
                self._repo.transition_state(record.id, PaperState.APPROVED)
                logger.info(
                    "Auto-approved paper %s (composite=%.4f)", record.id, composite
                )
            except InvalidStateTransitionError as exc:
                return AcquisitionOutcome(
                    paper_id=record.id,
                    success=False,
                    final_state=current_state,
                    failure_reason=str(exc),
                )
            return None  # proceed

        # Manual mode — APPROVED state required
        return AcquisitionOutcome(
            paper_id=record.id,
            success=False,
            final_state=current_state,
            failure_reason=(
                f"Manual approval required; paper is in {current_state.value!r} state"
            ),
        )

    def _check_caps(self, paper_id: UUID) -> AcquisitionOutcome | None:
        """
        Check weekly and monthly acquisition caps against the DB.
        Returns an AcquisitionOutcome if a cap is hit, else None.
        """
        now = datetime.now(timezone.utc)
        week_start = now - timedelta(days=7)
        month_start = now - timedelta(days=30)

        acquired_records = self._repo.get_by_state(PaperState.ACQUIRED)

        weekly = sum(
            1 for r in acquired_records
            if r.updated_at and _as_utc(r.updated_at) >= week_start
        )
        monthly = sum(
            1 for r in acquired_records
            if r.updated_at and _as_utc(r.updated_at) >= month_start
        )

        if weekly >= self._config.max_papers_per_week:
            reason = (
                f"Weekly acquisition cap reached "
                f"({weekly}/{self._config.max_papers_per_week})"
            )
            logger.warning(reason)
            return AcquisitionOutcome(
                paper_id=paper_id,
                success=False,
                final_state=PaperState.APPROVED,
                failure_reason=reason,
            )

        if monthly >= self._config.max_papers_per_month:
            reason = (
                f"Monthly acquisition cap reached "
                f"({monthly}/{self._config.max_papers_per_month})"
            )
            logger.warning(reason)
            return AcquisitionOutcome(
                paper_id=paper_id,
                success=False,
                final_state=PaperState.APPROVED,
                failure_reason=reason,
            )

        return None

    def _build_dest_path(self, record: PaperRecord) -> Path:
        """
        Build the final storage path for a paper's PDF.

        Structure: <storage_root>/<paper_id>/<arxiv_id or doi_slug>.pdf
        """
        paper_dir = self._storage_root / str(record.id)
        if record.arxiv_id:
            filename = f"{record.arxiv_id.replace('/', '_')}.pdf"
        elif record.doi:
            slug = record.doi.replace("/", "_").replace(":", "_")
            filename = f"{slug}.pdf"
        else:
            filename = f"{record.id}.pdf"
        return paper_dir / filename

    def _record_failure(self, record: PaperRecord, reason: str) -> None:
        """Append a failure entry to the record's evaluation JSON."""
        existing: dict = record.evaluation or {}
        failures: list = existing.get("acquisition_failures", [])
        failures.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
        })
        existing["acquisition_failures"] = failures
        record.evaluation = existing
        self._repo.session.flush()

    def _record_to_model(self, record: PaperRecord):
        """
        Build a minimal ResearchPaper Pydantic model from a PaperRecord.
        Only the fields needed by PdfResolver are populated.
        """
        from quantforge.models.paper import ResearchPaper, SourceReference, Author

        sources = []
        for src in (record.sources or []):
            try:
                sources.append(SourceReference(**src))
            except Exception:
                pass  # malformed source — skip

        return ResearchPaper(
            id=record.id,
            title=record.title,
            doi=record.doi,
            arxiv_id=record.arxiv_id,
            sources=sources,
            state=PaperState(record.state),
        )


def _as_utc(dt: datetime) -> datetime:
    """Ensure *dt* is timezone-aware (UTC)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt

