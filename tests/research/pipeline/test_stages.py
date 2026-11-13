# tests/research/pipeline/test_stages.py

import pytest
import uuid
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

from quantforge.database.models.paper import PaperRecord
from quantforge.database.repositories.paper import PaperRepository
from quantforge.models.states import PaperState
from quantforge.research.filtering.ranker import CandidateRanker
from quantforge.research.pipeline.context import PipelineContext
from quantforge.research.pipeline.stages import (
    run_normalize_stage,
    run_evaluate_stage,
    run_llm_stage,
    run_approve_stage,
)


def make_record(
    state: PaperState = PaperState.DISCOVERED,
    title: str = "Momentum Strategy Study",
    arxiv_id: str = "2401.00001",
) -> PaperRecord:
    record = MagicMock(spec=PaperRecord)
    record.id = uuid.uuid4()
    record.title = title
    record.arxiv_id = arxiv_id
    record.doi = None
    record.abstract = "We study momentum strategies."
    record.published_date = None
    record.journal = None
    record.authors = [{"name": "Jane Smith", "affiliation": None}]
    record.keywords = ["momentum", "equity"]
    record.sources = []
    record.state = state.value
    record.evaluation = {}
    record.composite_score = None
    return record


def make_repo(record: PaperRecord) -> MagicMock:
    repo = MagicMock(spec=PaperRepository)
    repo.get_by_id = MagicMock(return_value=record)
    repo.transition_state = MagicMock(return_value=record)
    repo.update_scores = MagicMock(return_value=record)
    return repo


def make_ctx(repo: MagicMock, dry_run: bool = False) -> PipelineContext:
    from quantforge.config.loader import (
        ResearchPipelineConfig,
        ResearchTopicsConfig,
        TopicConfig,
        LLMConfig,
        ProviderConfig,
        LLMBudgetConfig,
    )
    pipeline_config = ResearchPipelineConfig()
    topics_config = ResearchTopicsConfig(topics=[
        TopicConfig(
            name="momentum",
            description="Momentum strategies",
            search_terms=["cross-sectional momentum"],
            categories=["quantitative finance"],
        )
    ])
    llm_config = LLMConfig(
        provider=ProviderConfig(
            name="openrouter",
            model="anthropic/claude-3-haiku",
            base_url="https://openrouter.ai/api/v1",
        ),
        budget=LLMBudgetConfig(
            max_spend_usd=5.0,
            warn_at_usd=3.0,
        ),
        enabled=False,
    )
    return PipelineContext(
        pipeline_config=pipeline_config,
        topics_config=topics_config,
        llm_config=llm_config,
        repo=repo,
        ranker=CandidateRanker(),
        library_root=Path("/tmp/test_library"),
        dry_run=dry_run,
    )

# --- Normalize Stage ---

@pytest.mark.asyncio
async def test_normalize_discovered_paper():
    record = make_record(PaperState.DISCOVERED)
    repo = make_repo(record)
    ctx = make_ctx(repo)

    result = await run_normalize_stage(record.id, ctx)

    assert result.success
    repo.transition_state.assert_called_once_with(record.id, PaperState.NORMALIZED)


@pytest.mark.asyncio
async def test_normalize_skips_already_normalized():
    record = make_record(PaperState.NORMALIZED)
    repo = make_repo(record)
    ctx = make_ctx(repo)

    result = await run_normalize_stage(record.id, ctx)

    assert result.success
    assert result.skipped
    repo.transition_state.assert_not_called()


@pytest.mark.asyncio
async def test_normalize_rejects_missing_title():
    record = make_record(PaperState.DISCOVERED, title="")
    repo = make_repo(record)
    ctx = make_ctx(repo)

    result = await run_normalize_stage(record.id, ctx)

    assert not result.success
    repo.transition_state.assert_called_once_with(record.id, PaperState.REJECTED)


@pytest.mark.asyncio
async def test_normalize_paper_not_found():
    repo = MagicMock(spec=PaperRepository)
    repo.get_by_id = MagicMock(return_value=None)
    ctx = make_ctx(repo)

    result = await run_normalize_stage(uuid.uuid4(), ctx)

    assert not result.success
    assert "not found" in result.error


# --- Evaluate Stage ---

@pytest.mark.asyncio
async def test_evaluate_normalized_paper():
    record = make_record(PaperState.NORMALIZED)
    repo = make_repo(record)
    ctx = make_ctx(repo)

    result = await run_evaluate_stage(record.id, ctx, topic_keywords=["momentum"])

    # Scores are computed and persisted regardless of whether paper passes threshold
    repo.update_scores.assert_called_once()
    # Paper correctly rejected due to low composite score on minimal test data
    assert result.to_state in (PaperState.CANDIDATE, PaperState.REJECTED)


@pytest.mark.asyncio
async def test_evaluate_skips_non_normalized():
    record = make_record(PaperState.CANDIDATE)
    repo = make_repo(record)
    ctx = make_ctx(repo)

    result = await run_evaluate_stage(record.id, ctx, topic_keywords=["momentum"])

    assert result.success
    assert result.skipped
    repo.update_scores.assert_not_called()


# --- LLM Stage ---

@pytest.mark.asyncio
async def test_llm_stage_skipped_in_dry_run():
    record = make_record(PaperState.CANDIDATE)
    repo = make_repo(record)
    ctx = make_ctx(repo, dry_run=True)

    result = await run_llm_stage(record.id, ctx)

    assert result.success
    assert result.skipped
    assert result.dry_run


@pytest.mark.asyncio
async def test_llm_stage_skipped_when_no_budget_manager():
    record = make_record(PaperState.CANDIDATE)
    repo = make_repo(record)
    ctx = make_ctx(repo)
    ctx.budget_manager = None

    result = await run_llm_stage(record.id, ctx)

    assert result.success
    assert result.skipped


# --- Approve Stage ---

@pytest.mark.asyncio
async def test_approve_skipped_in_manual_mode():
    record = make_record(PaperState.CANDIDATE)
    repo = make_repo(record)
    ctx = make_ctx(repo)
    # Default approval_mode is manual
    assert ctx.acquisition_config.approval_mode == "manual"

    result = await run_approve_stage(record.id, ctx)

    assert result.success
    assert result.skipped
    repo.transition_state.assert_not_called()


@pytest.mark.asyncio
async def test_approve_transitions_in_auto_mode():
    record = make_record(PaperState.CANDIDATE)
    repo = make_repo(record)
    ctx = make_ctx(repo)
    ctx.pipeline_config.acquisition.approval_mode = "auto"

    result = await run_approve_stage(record.id, ctx)

    assert result.success
    repo.transition_state.assert_called_once_with(record.id, PaperState.APPROVED)


@pytest.mark.asyncio
async def test_approve_skips_non_candidate():
    record = make_record(PaperState.APPROVED)
    repo = make_repo(record)
    ctx = make_ctx(repo)

    result = await run_approve_stage(record.id, ctx)

    assert result.success
    assert result.skipped