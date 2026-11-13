# src/quantforge/research/pipeline/stages.py

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

from quantforge.database.models.paper import PaperRecord
from quantforge.database.repositories.paper import PaperRepository
from quantforge.models.paper import ResearchPaper, SourceReference, Author
from quantforge.models.states import PaperState
from quantforge.research.discovery.base import SearchQuery
from quantforge.research.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)


@dataclass
class StageResult:
    """Result of running one stage on one paper."""
    paper_id: UUID
    stage: str
    success: bool
    from_state: PaperState
    to_state: PaperState
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    error: str | None = None
    skipped: bool = False
    dry_run: bool = False

    def finish(self, success: bool, error: str | None = None) -> "StageResult":
        self.finished_at = datetime.now(timezone.utc)
        self.success = success
        self.error = error
        return self

    @property
    def duration_seconds(self) -> float | None:
        if self.finished_at and self.started_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None


def _paper_record_to_domain(record: PaperRecord) -> ResearchPaper:
    """Convert a PaperRecord ORM object to a ResearchPaper domain model."""
    authors = [
        Author(
            name=a.get("name", ""),
            affiliation=a.get("affiliation"),
        )
        for a in (record.authors or [])
    ]
    sources = [
        SourceReference(
            source_name=s.get("source_name", ""),
            source_id=s.get("source_id", ""),
            source_url=s.get("source_url"),
            raw_metadata=s.get("raw_metadata", {}),
        )
        for s in (record.sources or [])
    ]
    return ResearchPaper(
        id=record.id,
        title=record.title,
        doi=record.doi,
        arxiv_id=record.arxiv_id,
        abstract=record.abstract,
        published_date=record.published_date,
        journal=record.journal,
        authors=authors,
        keywords=record.keywords or [],
        sources=sources,
        state=PaperState(record.state),
    )


# ── Stage 1: Normalize ────────────────────────────────────────────────────────

async def run_normalize_stage(
    paper_id: UUID,
    ctx: PipelineContext,
) -> StageResult:
    """
    DISCOVERED → NORMALIZED
    Validates that the record has minimum required fields.
    In a full implementation this also runs deduplication against existing records.
    """
    result = StageResult(
        paper_id=paper_id,
        stage="normalize",
        success=False,
        from_state=PaperState.DISCOVERED,
        to_state=PaperState.NORMALIZED,
    )

    try:
        record = ctx.repo.get_by_id(paper_id)
        if record is None:
            return result.finish(False, f"Paper {paper_id} not found")

        current = PaperState(record.state)
        if current != PaperState.DISCOVERED:
            result.skipped = True
            return result.finish(True)

        # Minimum required fields
        if not record.title or not record.title.strip():
            ctx.repo.transition_state(paper_id, PaperState.REJECTED)
            return result.finish(False, "Missing required field: title")

        ctx.repo.transition_state(paper_id, PaperState.NORMALIZED)
        return result.finish(True)

    except Exception as e:
        logger.error("Normalize stage failed for %s: %s", paper_id, e)
        return result.finish(False, str(e))


# ── Stage 2: Evaluate (Score) ─────────────────────────────────────────────────

async def run_evaluate_stage(
    paper_id: UUID,
    ctx: PipelineContext,
    topic_keywords: list[str],
) -> StageResult:
    """
    NORMALIZED → EVALUATED → CANDIDATE or REJECTED
    Runs deterministic scoring and applies score threshold filter.
    """
    result = StageResult(
        paper_id=paper_id,
        stage="evaluate",
        success=False,
        from_state=PaperState.NORMALIZED,
        to_state=PaperState.CANDIDATE,
    )

    try:
        record = ctx.repo.get_by_id(paper_id)
        if record is None:
            return result.finish(False, f"Paper {paper_id} not found")

        current = PaperState(record.state)
        if current not in (PaperState.NORMALIZED,):
            result.skipped = True
            return result.finish(True)

        paper = _paper_record_to_domain(record)
        candidates = ctx.ranker.rank([paper], topic_keywords=topic_keywords)

        if not candidates:
            ctx.repo.transition_state(paper_id, PaperState.REJECTED)
            return result.finish(False, "Paper filtered out during ranking")

        candidate = candidates[0]
        ctx.repo.update_scores(
            paper_id,
            relevance=candidate.relevance_score,
            credibility=candidate.credibility_score,
            implementability=candidate.implementability_score,
            reproducibility=candidate.reproducibility_score,
            composite=candidate.composite_score,
        )

        ctx.repo.transition_state(paper_id, PaperState.EVALUATED)

        # Apply score threshold
        min_score = ctx.filtering_config.min_acquisition_score
        if candidate.composite_score < min_score:
            ctx.repo.transition_state(paper_id, PaperState.REJECTED)
            result.to_state = PaperState.REJECTED
            return result.finish(
                False,
                f"Composite score {candidate.composite_score:.4f} below "
                f"threshold {min_score}",
            )

        ctx.repo.transition_state(paper_id, PaperState.CANDIDATE)
        return result.finish(True)

    except Exception as e:
        logger.error("Evaluate stage failed for %s: %s", paper_id, e)
        return result.finish(False, str(e))


# ── Stage 3: LLM Priority Evaluation ─────────────────────────────────────────

async def run_llm_stage(
    paper_id: UUID,
    ctx: PipelineContext,
) -> StageResult:
    """
    CANDIDATE — adds LLM priority score (optional, non-blocking).
    Does not change state — paper remains CANDIDATE.
    Skipped entirely in dry_run mode.
    """
    result = StageResult(
        paper_id=paper_id,
        stage="llm_evaluate",
        success=False,
        from_state=PaperState.CANDIDATE,
        to_state=PaperState.CANDIDATE,
    )

    if ctx.dry_run:
        result.dry_run = True
        result.skipped = True
        return result.finish(True)

    if ctx.budget_manager is None or not ctx.llm_config.enabled:
        result.skipped = True
        return result.finish(True)

    try:
        from quantforge.llm.evaluator import LLMEvaluator
        from quantforge.models.evaluation import PaperEvaluation

        record = ctx.repo.get_by_id(paper_id)
        if record is None:
            return result.finish(False, f"Paper {paper_id} not found")

        current = PaperState(record.state)
        if current != PaperState.CANDIDATE:
            result.skipped = True
            return result.finish(True)

        paper = _paper_record_to_domain(record)
        evaluation = PaperEvaluation(paper_id=paper_id)
        evaluator = LLMEvaluator(ctx.budget_manager)
        evaluation = await evaluator.evaluate_priority(paper, evaluation)

        if evaluation.priority is not None:
            existing_eval = record.evaluation or {}
            existing_eval["priority"] = {
                "score": evaluation.priority.score,
                "rationale": evaluation.priority.rationale,
                "evidence": evaluation.priority.evidence,
                "llm_assisted": True,
            }
            record.evaluation = existing_eval

        return result.finish(True)

    except Exception as e:
        logger.error("LLM stage failed for %s: %s", paper_id, e)
        return result.finish(False, str(e))


# ── Stage 4: Approve ──────────────────────────────────────────────────────────

async def run_approve_stage(
    paper_id: UUID,
    ctx: PipelineContext,
) -> StageResult:
    """
    CANDIDATE → APPROVED
    In auto mode: approves all candidates above threshold.
    In manual mode: skips — approval must happen externally.
    """
    result = StageResult(
        paper_id=paper_id,
        stage="approve",
        success=False,
        from_state=PaperState.CANDIDATE,
        to_state=PaperState.APPROVED,
    )

    try:
        record = ctx.repo.get_by_id(paper_id)
        if record is None:
            return result.finish(False, f"Paper {paper_id} not found")

        current = PaperState(record.state)
        if current != PaperState.CANDIDATE:
            result.skipped = True
            return result.finish(True)

        if ctx.acquisition_config.approval_mode == "manual":
            result.skipped = True
            logger.info(
                "Paper %s awaiting manual approval (approval_mode=manual)",
                paper_id,
            )
            return result.finish(True)

        ctx.repo.transition_state(paper_id, PaperState.APPROVED)
        return result.finish(True)

    except Exception as e:
        logger.error("Approve stage failed for %s: %s", paper_id, e)
        return result.finish(False, str(e))


# ── Stage 5: Acquire ──────────────────────────────────────────────────────────

async def run_acquire_stage(
    paper_id: UUID,
    ctx: PipelineContext,
) -> StageResult:
    """
    APPROVED → ACQUIRED
    Downloads and validates the PDF.
    Skipped in dry_run mode — logs what would have been downloaded.
    """
    result = StageResult(
        paper_id=paper_id,
        stage="acquire",
        success=False,
        from_state=PaperState.APPROVED,
        to_state=PaperState.ACQUIRED,
    )

    try:
        record = ctx.repo.get_by_id(paper_id)
        if record is None:
            return result.finish(False, f"Paper {paper_id} not found")

        current = PaperState(record.state)
        if current != PaperState.APPROVED:
            result.skipped = True
            return result.finish(True)

        if ctx.dry_run:
            result.dry_run = True
            logger.info(
                "DRY RUN: would acquire paper %s (arxiv_id=%s)",
                paper_id, record.arxiv_id,
            )
            result.skipped = True
            return result.finish(True)

        from quantforge.research.acquisition.manager import AcquisitionManager
        import httpx

        async with httpx.AsyncClient() as client:
            manager = AcquisitionManager(
                repo=ctx.repo,
                config=ctx.acquisition_config,
                storage_root=ctx.library_root,
                client=client,
            )
            acq_result = await manager.acquire(paper_id=paper_id)

        if not acq_result.success:
            return result.finish(False, acq_result.failure_reason)

        return result.finish(True)

    except Exception as e:
        logger.error("Acquire stage failed for %s: %s", paper_id, e)
        return result.finish(False, str(e))


# ── Stage 6: Process ──────────────────────────────────────────────────────────

async def run_process_stage(
    paper_id: UUID,
    ctx: PipelineContext,
) -> StageResult:
    """
    ACQUIRED → PROCESSED
    Extracts text and sections, assembles Research Document Package.
    """
    result = StageResult(
        paper_id=paper_id,
        stage="process",
        success=False,
        from_state=PaperState.ACQUIRED,
        to_state=PaperState.PROCESSED,
    )

    try:
        record = ctx.repo.get_by_id(paper_id)
        if record is None:
            return result.finish(False, f"Paper {paper_id} not found")

        current = PaperState(record.state)
        if current != PaperState.ACQUIRED:
            result.skipped = True
            return result.finish(True)

        from quantforge.research.processing.processor import PdfProcessor

        processor = PdfProcessor(storage_root=ctx.library_root)
        proc_result = processor.process(paper_id=paper_id, repo=ctx.repo)

        if not proc_result.success:
            return result.finish(False, proc_result.error)

        return result.finish(True)

    except Exception as e:
        logger.error("Process stage failed for %s: %s", paper_id, e)
        return result.finish(False, str(e))