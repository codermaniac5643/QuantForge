# tests/research/pipeline/test_orchestrator.py

import pytest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

from quantforge.models.states import PaperState
from quantforge.research.pipeline.orchestrator import ResearchOrchestrator
from quantforge.research.pipeline.context import PipelineContext
from quantforge.research.filtering.ranker import CandidateRanker


def make_ctx(dry_run: bool = False) -> PipelineContext:
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
        budget=LLMBudgetConfig(max_spend_usd=5.0, warn_at_usd=3.0),
        enabled=False,
    )
    repo = MagicMock()
    repo.get_by_id = MagicMock(return_value=None)
    repo.get_by_arxiv_id = MagicMock(return_value=None)
    repo.get_by_doi = MagicMock(return_value=None)
    repo.create = MagicMock()

    return PipelineContext(
        pipeline_config=pipeline_config,
        topics_config=topics_config,
        llm_config=llm_config,
        repo=repo,
        ranker=CandidateRanker(),
        library_root=Path("/tmp/test_library"),
        dry_run=dry_run,
    )


def make_discovery_result(n_papers: int = 2):
    from quantforge.models.paper import ResearchPaper, SourceReference
    from quantforge.research.discovery.base import DiscoveryResult, SearchQuery

    papers = [
        ResearchPaper(
            title=f"Momentum Paper {i}",
            arxiv_id=f"2401.0000{i}",
            abstract="We study momentum.",
            keywords=["momentum"],
            sources=[SourceReference(
                source_name="arxiv",
                source_id=f"2401.0000{i}",
                source_url=f"https://arxiv.org/abs/2401.0000{i}",
            )],
        )
        for i in range(n_papers)
    ]

    query = SearchQuery(
        term="cross-sectional momentum",
        topic_name="momentum",
    )
    result = DiscoveryResult(
        source_name="arxiv",
        query=query,
        papers=papers,
        total_found=n_papers,
        total_fetched=n_papers,
    )
    return result


# --- Active Topics ---

def test_active_topics_returns_all_when_no_filter():
    ctx = make_ctx()
    topics = ctx.active_topics()
    assert len(topics) == 1
    assert topics[0].name == "momentum"


def test_active_topics_filters_by_name():
    ctx = make_ctx()
    ctx.topic_names = ["other_topic"]
    topics = ctx.active_topics()
    assert len(topics) == 0


# --- Discovery ---

@pytest.mark.asyncio
async def test_run_discovers_and_persists_papers():
    ctx = make_ctx()
    orchestrator = ResearchOrchestrator(ctx)

    discovery_result = make_discovery_result(n_papers=2)

    with patch(
        "quantforge.research.pipeline.orchestrator.ArxivConnector"
    ) as mock_connector_cls:
        mock_connector = AsyncMock()
        mock_connector.search = AsyncMock(return_value=discovery_result)
        mock_connector.__aenter__ = AsyncMock(return_value=mock_connector)
        mock_connector.__aexit__ = AsyncMock(return_value=None)
        mock_connector_cls.return_value = mock_connector

        with patch.object(orchestrator, "_run_paper_stages", new=AsyncMock()):
            summary = await orchestrator.run()

    assert summary.total_discovered == 2
    assert summary.total_persisted == 2


@pytest.mark.asyncio
async def test_run_skips_already_processed_papers():
    ctx = make_ctx()

    # Simulate paper already in DB as PROCESSED
    from unittest.mock import MagicMock
    existing = MagicMock()
    existing.id = uuid.uuid4()
    existing.state = PaperState.PROCESSED.value
    ctx.repo.get_by_arxiv_id = MagicMock(return_value=existing)

    orchestrator = ResearchOrchestrator(ctx)
    discovery_result = make_discovery_result(n_papers=1)

    with patch(
        "quantforge.research.pipeline.orchestrator.ArxivConnector"
    ) as mock_connector_cls:
        mock_connector = AsyncMock()
        mock_connector.search = AsyncMock(return_value=discovery_result)
        mock_connector.__aenter__ = AsyncMock(return_value=mock_connector)
        mock_connector.__aexit__ = AsyncMock(return_value=None)
        mock_connector_cls.return_value = mock_connector

        with patch.object(orchestrator, "_run_paper_stages", new=AsyncMock()):
            summary = await orchestrator.run()

    # Already processed — not added to pipeline
    assert summary.total_persisted == 0


@pytest.mark.asyncio
async def test_run_summary_has_timing():
    ctx = make_ctx()
    orchestrator = ResearchOrchestrator(ctx)

    with patch(
        "quantforge.research.pipeline.orchestrator.ArxivConnector"
    ) as mock_connector_cls:
        mock_connector = AsyncMock()
        mock_connector.search = AsyncMock(
            return_value=make_discovery_result(0)
        )
        mock_connector.__aenter__ = AsyncMock(return_value=mock_connector)
        mock_connector.__aexit__ = AsyncMock(return_value=None)
        mock_connector_cls.return_value = mock_connector

        summary = await orchestrator.run()

    assert summary.started_at is not None
    assert summary.finished_at is not None
    assert summary.duration_seconds >= 0


@pytest.mark.asyncio
async def test_dry_run_flag_propagates():
    ctx = make_ctx(dry_run=True)
    assert ctx.dry_run is True
    orchestrator = ResearchOrchestrator(ctx)

    with patch(
        "quantforge.research.pipeline.orchestrator.ArxivConnector"
    ) as mock_connector_cls:
        mock_connector = AsyncMock()
        mock_connector.search = AsyncMock(
            return_value=make_discovery_result(0)
        )
        mock_connector.__aenter__ = AsyncMock(return_value=mock_connector)
        mock_connector.__aexit__ = AsyncMock(return_value=None)
        mock_connector_cls.return_value = mock_connector

        summary = await orchestrator.run()

    assert summary.run_id is not None