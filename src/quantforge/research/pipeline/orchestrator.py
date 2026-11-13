# src/quantforge/research/pipeline/orchestrator.py

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from quantforge.database.connection import get_session
from quantforge.database.models.paper import PaperRecord
from quantforge.database.repositories.paper import PaperRepository
from quantforge.models.paper import ResearchPaper, SourceReference, Author
from quantforge.models.states import PaperState
from quantforge.research.discovery.arxiv import ArxivConnector
from quantforge.research.discovery.base import SearchQuery
from quantforge.research.pipeline.context import PipelineContext
from quantforge.research.pipeline.retry import (
    DISCOVERY_RETRY,
    ACQUISITION_RETRY,
    PROCESSING_RETRY,
    LLM_RETRY,
    NO_RETRY,
    with_retry,
)
from quantforge.research.pipeline.stages import (
    StageResult,
    run_normalize_stage,
    run_evaluate_stage,
    run_llm_stage,
    run_approve_stage,
    run_acquire_stage,
    run_process_stage,
)

logger = logging.getLogger(__name__)


@dataclass
class PaperRunRecord:
    """Tracks all stage results for one paper in a pipeline run."""
    paper_id: UUID
    title: str
    arxiv_id: str | None
    stage_results: list[StageResult] = field(default_factory=list)

    @property
    def final_state(self) -> str | None:
        for r in reversed(self.stage_results):
            if r.finished_at is not None:
                return r.to_state.value
        return None

    @property
    def failed_stages(self) -> list[StageResult]:
        return [r for r in self.stage_results if not r.success and not r.skipped]

    @property
    def success(self) -> bool:
        return all(r.success for r in self.stage_results)


@dataclass
class PipelineRunSummary:
    """Summary of a complete pipeline run."""
    run_id: str
    started_at: datetime
    finished_at: datetime | None = None
    total_discovered: int = 0
    total_persisted: int = 0
    total_processed: int = 0
    total_rejected: int = 0
    total_failed: int = 0
    paper_records: list[PaperRunRecord] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float | None:
        if self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None


class ResearchOrchestrator:
    """
    Runs the full research pipeline for a batch of papers.

    Responsibilities:
    - Discover papers from configured sources
    - Persist new papers to the database
    - Run each stage in sequence: normalize → evaluate → llm → approve → acquire → process
    - Skip papers already past a stage (resume support)
    - Record timing and errors for every stage attempt
    - Never let one paper's failure stop others
    - Respect dry_run mode for acquisition and LLM stages
    """

    def __init__(self, ctx: PipelineContext) -> None:
        self._ctx = ctx

    async def run(self) -> PipelineRunSummary:
        """Execute a full pipeline run across all active topics."""
        run_id = str(uuid.uuid4())[:8]
        summary = PipelineRunSummary(
            run_id=run_id,
            started_at=datetime.now(timezone.utc),
        )

        logger.info(
            "Pipeline run %s starting — topics=%s dry_run=%s",
            run_id,
            [t.name for t in self._ctx.active_topics()],
            self._ctx.dry_run,
        )

        # Step 1 — Discovery: fetch papers from sources
        discovered_papers = await self._discover_papers()
        summary.total_discovered = len(discovered_papers)
        logger.info("Discovered %d papers total", summary.total_discovered)

        # Step 2 — Persist: save new papers to DB
        paper_ids = self._persist_papers(discovered_papers)
        summary.total_persisted = len(paper_ids)
        logger.info("Persisted %d new papers", summary.total_persisted)

        # Step 3 — Run pipeline stages for each paper
        topic_keywords = [
            term
            for topic in self._ctx.active_topics()
            for term in topic.search_terms
        ]

        for paper_id in paper_ids:
            record = self._ctx.repo.get_by_id(paper_id)
            if record is None:
                continue

            paper_run = PaperRunRecord(
                paper_id=paper_id,
                title=record.title,
                arxiv_id=record.arxiv_id,
            )

            await self._run_paper_stages(
                paper_id=paper_id,
                paper_run=paper_run,
                topic_keywords=topic_keywords,
            )

            summary.paper_records.append(paper_run)

            final = paper_run.final_state
            if final == PaperState.PROCESSED.value:
                summary.total_processed += 1
            elif final == PaperState.REJECTED.value:
                summary.total_rejected += 1
            elif paper_run.failed_stages:
                summary.total_failed += 1

        summary.finished_at = datetime.now(timezone.utc)
        logger.info(
            "Pipeline run %s complete in %.1fs — "
            "discovered=%d persisted=%d processed=%d rejected=%d failed=%d",
            run_id,
            summary.duration_seconds,
            summary.total_discovered,
            summary.total_persisted,
            summary.total_processed,
            summary.total_rejected,
            summary.total_failed,
        )
        return summary

    async def _discover_papers(self) -> list[ResearchPaper]:
        """Discover papers from arXiv for all active topics."""
        all_papers: list[ResearchPaper] = []
        disc_cfg = self._ctx.discovery_config

        for topic in self._ctx.active_topics():
            for term in topic.search_terms:
                query = SearchQuery(
                    term=term,
                    topic_name=topic.name,
                    max_results=disc_cfg.results_per_term,
                    lookback_days=disc_cfg.lookback_days,
                )
                try:
                    async def _search(q=query):
                        async with ArxivConnector(
                            request_delay=disc_cfg.request_delay_seconds,
                            timeout=disc_cfg.timeout_seconds,
                            max_retries=disc_cfg.max_retries,
                        ) as connector:
                            return await connector.search(q)

                    result = await with_retry(
                        _search,
                        DISCOVERY_RETRY,
                        label=f"arXiv search [{term}]",
                    )

                    if result.success:
                        all_papers.extend(result.papers)
                        logger.info(
                            "Topic %r term %r — found %d papers",
                            topic.name, term, len(result.papers),
                        )
                    else:
                        logger.warning(
                            "Discovery failed for term %r: %s",
                            term, result.errors,
                        )

                except Exception as e:
                    logger.error(
                        "Discovery error for term %r: %s", term, e
                    )

        return all_papers

    def _persist_papers(self, papers: list[ResearchPaper]) -> list[UUID]:
        """
        Persist newly discovered papers to the database.
        Skips papers that already exist (by arXiv ID or DOI).
        Returns list of paper IDs to process (new + existing not yet complete).
        """
        from quantforge.database.repositories.paper import DuplicatePaperError
        from quantforge.research.normalization.text import (
            normalize_arxiv_id, normalize_doi
        )

        paper_ids: list[UUID] = []

        for paper in papers:
            try:
                # Check for existing record
                existing = None
                if paper.arxiv_id:
                    norm_id = normalize_arxiv_id(paper.arxiv_id) or paper.arxiv_id
                    existing = self._ctx.repo.get_by_arxiv_id(norm_id)
                if existing is None and paper.doi:
                    existing = self._ctx.repo.get_by_doi(paper.doi)

                if existing is not None:
                    # Include if not yet in a terminal state
                    state = PaperState(existing.state)
                    if state not in (PaperState.PROCESSED, PaperState.REJECTED):
                        paper_ids.append(existing.id)
                    continue

                record = PaperRecord(
                    id=paper.id,
                    title=paper.title,
                    doi=paper.doi,
                    arxiv_id=paper.arxiv_id,
                    abstract=paper.abstract,
                    published_date=paper.published_date,
                    journal=paper.journal,
                    authors=[
                        {"name": a.name, "affiliation": a.affiliation}
                        for a in paper.authors
                    ],
                    keywords=paper.keywords,
                    sources=[
                        {
                            "source_name": s.source_name,
                            "source_id": s.source_id,
                            "source_url": str(s.source_url) if s.source_url else None,
                            "raw_metadata": s.raw_metadata,
                        }
                        for s in paper.sources
                    ],
                    state=PaperState.DISCOVERED.value,
                )
                self._ctx.repo.create(record)
                paper_ids.append(paper.id)

            except DuplicatePaperError:
                pass
            except Exception as e:
                logger.error(
                    "Failed to persist paper %r: %s", paper.title[:60], e
                )

        return paper_ids

    async def _run_paper_stages(
        self,
        paper_id: UUID,
        paper_run: PaperRunRecord,
        topic_keywords: list[str],
    ) -> None:
        """
        Run all pipeline stages for one paper.
        Each stage is independently retried per its retry policy.
        A failure in any stage stops further stages for this paper.
        """
        ctx = self._ctx

        stages = [
            ("normalize",    lambda: run_normalize_stage(paper_id, ctx),          NO_RETRY),
            ("evaluate",     lambda: run_evaluate_stage(paper_id, ctx, topic_keywords), NO_RETRY),
            ("llm_evaluate", lambda: run_llm_stage(paper_id, ctx),                LLM_RETRY),
            ("approve",      lambda: run_approve_stage(paper_id, ctx),            NO_RETRY),
            ("acquire",      lambda: run_acquire_stage(paper_id, ctx),            ACQUISITION_RETRY),
            ("process",      lambda: run_process_stage(paper_id, ctx),            PROCESSING_RETRY),
        ]

        for stage_name, stage_fn, retry_policy in stages:
            try:
                stage_result = await with_retry(
                    stage_fn,
                    retry_policy,
                    label=f"[{stage_name}] paper={paper_id}",
                )
                paper_run.stage_results.append(stage_result)

                if not stage_result.success and not stage_result.skipped:
                    logger.warning(
                        "Paper %s failed at stage %r: %s — stopping further stages",
                        paper_id, stage_name, stage_result.error,
                    )
                    break

            except Exception as e:
                # Unhandled exception from stage — record and stop
                failed = StageResult(
                    paper_id=paper_id,
                    stage=stage_name,
                    success=False,
                    from_state=PaperState.DISCOVERED,
                    to_state=PaperState.DISCOVERED,
                    error=str(e),
                )
                failed.finish(False, str(e))
                paper_run.stage_results.append(failed)
                logger.error(
                    "Unhandled error in stage %r for paper %s: %s",
                    stage_name, paper_id, e,
                )
                break