# tests/integration/conftest.py

import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from quantforge.config.loader import (
    ResearchPipelineConfig,
    ResearchTopicsConfig,
    TopicConfig,
    LLMConfig,
    ProviderConfig,
    LLMBudgetConfig,
)
from quantforge.database.base import Base
from quantforge.database.connection import get_session
from quantforge.database.models.paper import PaperRecord
from quantforge.database.repositories.paper import PaperRepository
from quantforge.models.states import PaperState
from quantforge.research.filtering.ranker import CandidateRanker
from quantforge.research.pipeline.context import PipelineContext

TEST_DB_URL = "postgresql://postgres:postgres@localhost:5432/quantforge_test"


@pytest.fixture(scope="session")
def int_engine():
    """Real test database engine — tables created once per session."""
    engine = create_engine(TEST_DB_URL)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(scope="function")
def int_session(int_engine):
    """
    Transactional session per test — rolled back after each test.
    Nothing persists between integration tests.
    """
    connection = int_engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection)
    session = Session()
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture(scope="function")
def int_repo(int_session):
    """PaperRepository backed by the transactional test session."""
    return PaperRepository(int_session)


@pytest.fixture(scope="function")
def library_root(tmp_path) -> Path:
    """Temporary directory for PDF storage during integration tests."""
    root = tmp_path / "research_library"
    root.mkdir()
    return root


@pytest.fixture(scope="function")
def pipeline_config() -> ResearchPipelineConfig:
    config = ResearchPipelineConfig()
    config.acquisition.approval_mode = "auto"
    config.acquisition.max_papers_per_week = 100
    config.acquisition.max_papers_per_month = 100
    return config


@pytest.fixture(scope="function")
def topics_config() -> ResearchTopicsConfig:
    return ResearchTopicsConfig(topics=[
        TopicConfig(
            name="momentum",
            description="Momentum strategies",
            search_terms=["cross-sectional momentum", "price momentum"],
            categories=["quantitative finance"],
        )
    ])


@pytest.fixture(scope="function")
def llm_config() -> LLMConfig:
    return LLMConfig(
        provider=ProviderConfig(
            name="openrouter",
            model="anthropic/claude-3-haiku",
            base_url="https://openrouter.ai/api/v1",
        ),
        budget=LLMBudgetConfig(
            max_spend_usd=0.0,   # zero budget — LLM always skipped
            warn_at_usd=0.0,
        ),
        enabled=False,
    )


@pytest.fixture(scope="function")
def pipeline_ctx(
    int_repo,
    pipeline_config,
    topics_config,
    llm_config,
    library_root,
) -> PipelineContext:
    return PipelineContext(
        pipeline_config=pipeline_config,
        topics_config=topics_config,
        llm_config=llm_config,
        repo=int_repo,
        ranker=CandidateRanker(),
        library_root=library_root,
        dry_run=True,   # no real downloads in integration tests
    )


def make_paper_record(
    state: PaperState = PaperState.DISCOVERED,
    title: str = "Cross-Sectional Momentum in Equity Markets",
    arxiv_id: str | None = None,
    doi: str | None = None,
    composite_score: float | None = None,
) -> PaperRecord:
    """Helper — creates a minimal valid PaperRecord for integration tests."""
    return PaperRecord(
        id=uuid.uuid4(),
        title=title,
        arxiv_id=arxiv_id or f"2401.{uuid.uuid4().hex[:5]}",
        doi=doi,
        abstract="We study momentum strategies across equity markets using daily return data.",
        authors=[{"name": "Jane Smith", "affiliation": "MIT"}],
        keywords=["momentum", "equity", "cross-sectional"],
        sources=[{
            "source_name": "arxiv",
            "source_id": arxiv_id or "2401.00001",
            "source_url": "https://arxiv.org/abs/2401.00001",
            "raw_metadata": {"topic": "momentum", "search_term": "cross-sectional momentum"},
        }],
        state=state.value,
        composite_score=composite_score,
    )