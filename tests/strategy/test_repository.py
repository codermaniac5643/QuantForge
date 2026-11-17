# tests/strategy/test_repository.py
"""
Integration tests for StrategyRepository — Milestone 12.
Requires a live PostgreSQL test database (quantforge_test).
Tests run inside a transaction that is rolled back after each test.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from quantforge.database.base import Base
from quantforge.database.models.paper import PaperRecord  # noqa: F401 — needed for FK
from quantforge.database.models.strategy import StrategyRecord
from quantforge.database.repositories.strategy import (
    DuplicateStrategyError,
    InvalidStatusTransitionError,
    StrategyRepository,
)
from quantforge.models.states import PaperState
from quantforge.strategy.models.enums import (
    CompletenessClass,
    ExtractionMethod,
    StrategyStatus,
)

TEST_DB_URL = "postgresql://postgres:postgres@localhost:5432/quantforge_test"


# ---------------------------------------------------------------------------
# Session fixtures (mirror the pattern in tests/integration/conftest.py)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def strat_engine():
    engine = create_engine(TEST_DB_URL)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(scope="function")
def strat_session(strat_engine):
    connection = strat_engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection)
    session = Session()
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture(scope="function")
def strat_repo(strat_session) -> StrategyRepository:
    return StrategyRepository(strat_session)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_paper(session) -> PaperRecord:
    """Insert a minimal PaperRecord so foreign-key constraints pass."""
    paper = PaperRecord(
        id=uuid.uuid4(),
        title="Momentum Strategy Test Paper",
        arxiv_id=f"test.{uuid.uuid4().hex[:8]}",
        abstract="Abstract.",
        authors=[],
        keywords=[],
        sources=[],
        state=PaperState.PROCESSED.value,
    )
    session.add(paper)
    session.flush()
    return paper


def _make_strategy_record(
    paper_id: uuid.UUID,
    version: int = 1,
    variant: str = "base",
    status: StrategyStatus = StrategyStatus.DRAFT,
) -> StrategyRecord:
    return StrategyRecord(
        id=uuid.uuid4(),
        paper_id=paper_id,
        version=version,
        variant=variant,
        status=status.value,
        completeness_class=CompletenessClass.ENTRY_FOCUSED.value,
        extracted_by=ExtractionMethod.LLM_ASSISTED.value,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStrategyRepositoryCreate:
    def test_create_and_get_by_id(self, strat_repo, strat_session):
        paper = _make_paper(strat_session)
        record = _make_strategy_record(paper.id)
        created = strat_repo.create(record)
        assert created.id == record.id

        fetched = strat_repo.get_by_id(created.id)
        assert fetched is not None
        assert fetched.paper_id == paper.id
        assert fetched.version == 1

    def test_get_by_id_missing_returns_none(self, strat_repo):
        result = strat_repo.get_by_id(uuid.uuid4())
        assert result is None

    def test_duplicate_paper_version_variant_raises(self, strat_repo, strat_session):
        paper = _make_paper(strat_session)
        r1 = _make_strategy_record(paper.id, version=1, variant="base")
        strat_repo.create(r1)

        r2 = _make_strategy_record(paper.id, version=1, variant="base")
        with pytest.raises(DuplicateStrategyError):
            strat_repo.create(r2)

    def test_different_variant_same_version_ok(self, strat_repo, strat_session):
        paper = _make_paper(strat_session)
        r1 = _make_strategy_record(paper.id, version=1, variant="base")
        r2 = _make_strategy_record(paper.id, version=1, variant="enhanced")
        strat_repo.create(r1)
        strat_repo.create(r2)
        rows = strat_repo.get_by_paper_id(paper.id)
        assert len(rows) == 2


class TestStrategyRepositoryGetByPaper:
    def test_get_by_paper_id_returns_all(self, strat_repo, strat_session):
        paper = _make_paper(strat_session)
        strat_repo.create(_make_strategy_record(paper.id, version=1))
        strat_repo.create(_make_strategy_record(paper.id, version=2))
        rows = strat_repo.get_by_paper_id(paper.id)
        assert len(rows) == 2

    def test_get_by_paper_id_empty(self, strat_repo):
        rows = strat_repo.get_by_paper_id(uuid.uuid4())
        assert rows == []

    def test_get_latest_base_returns_highest_version(self, strat_repo, strat_session):
        paper = _make_paper(strat_session)
        strat_repo.create(_make_strategy_record(paper.id, version=1))
        strat_repo.create(_make_strategy_record(paper.id, version=2))
        latest = strat_repo.get_latest_base(paper.id)
        assert latest is not None
        assert latest.version == 2

    def test_get_latest_base_excludes_superseded(self, strat_repo, strat_session):
        paper = _make_paper(strat_session)
        old = _make_strategy_record(paper.id, version=1, status=StrategyStatus.SUPERSEDED)
        strat_repo.create(old)
        result = strat_repo.get_latest_base(paper.id)
        assert result is None

    def test_list_by_paper_alias(self, strat_repo, strat_session):
        paper = _make_paper(strat_session)
        strat_repo.create(_make_strategy_record(paper.id))
        assert strat_repo.list_by_paper(paper.id) == strat_repo.get_by_paper_id(paper.id)


class TestStrategyRepositoryListByStatus:
    def test_list_by_status_filters(self, strat_repo, strat_session):
        paper = _make_paper(strat_session)
        strat_repo.create(_make_strategy_record(paper.id, version=1, status=StrategyStatus.DRAFT))
        strat_repo.create(_make_strategy_record(paper.id, version=2, status=StrategyStatus.UNDER_REVIEW))

        drafts = strat_repo.list_by_status(StrategyStatus.DRAFT)
        reviews = strat_repo.list_by_status(StrategyStatus.UNDER_REVIEW)

        assert any(r.version == 1 for r in drafts)
        assert any(r.version == 2 for r in reviews)
        assert all(r.status == StrategyStatus.DRAFT.value for r in drafts)

    def test_list_by_status_empty(self, strat_repo):
        result = strat_repo.list_by_status(StrategyStatus.APPROVED)
        assert result == []


class TestStrategyRepositoryUpdateStatus:
    def test_valid_transition_draft_to_under_review(self, strat_repo, strat_session):
        paper = _make_paper(strat_session)
        record = strat_repo.create(_make_strategy_record(paper.id))
        updated = strat_repo.update_status(record.id, StrategyStatus.UNDER_REVIEW)
        assert updated.status == StrategyStatus.UNDER_REVIEW.value

    def test_valid_transition_with_reviewer(self, strat_repo, strat_session):
        paper = _make_paper(strat_session)
        record = strat_repo.create(_make_strategy_record(paper.id))
        strat_repo.update_status(record.id, StrategyStatus.UNDER_REVIEW)
        approved = strat_repo.update_status(
            record.id, StrategyStatus.APPROVED, reviewer_id="analyst_01"
        )
        assert approved.reviewed_by == "analyst_01"

    def test_invalid_transition_raises(self, strat_repo, strat_session):
        paper = _make_paper(strat_session)
        record = strat_repo.create(_make_strategy_record(paper.id))
        with pytest.raises(InvalidStatusTransitionError):
            strat_repo.update_status(record.id, StrategyStatus.APPROVED)  # skip step

    def test_update_status_not_found_raises(self, strat_repo):
        with pytest.raises(ValueError, match="not found"):
            strat_repo.update_status(uuid.uuid4(), StrategyStatus.UNDER_REVIEW)

    def test_terminal_superseded_cannot_transition(self, strat_repo, strat_session):
        paper = _make_paper(strat_session)
        record = _make_strategy_record(paper.id, status=StrategyStatus.SUPERSEDED)
        strat_repo.create(record)
        with pytest.raises(InvalidStatusTransitionError):
            strat_repo.update_status(record.id, StrategyStatus.DRAFT)


class TestStrategyRepositoryNextVersion:
    def test_next_version_no_existing(self, strat_repo):
        result = strat_repo.next_version(uuid.uuid4())
        assert result == 1

    def test_next_version_increments(self, strat_repo, strat_session):
        paper = _make_paper(strat_session)
        strat_repo.create(_make_strategy_record(paper.id, version=1))
        strat_repo.create(_make_strategy_record(paper.id, version=2))
        assert strat_repo.next_version(paper.id) == 3

    def test_next_version_per_variant(self, strat_repo, strat_session):
        paper = _make_paper(strat_session)
        strat_repo.create(_make_strategy_record(paper.id, version=1, variant="base"))
        strat_repo.create(_make_strategy_record(paper.id, version=1, variant="enhanced"))
        # base has version=1, so next is 2
        assert strat_repo.next_version(paper.id, variant="base") == 2
        # enhanced has version=1, so next is 2
        assert strat_repo.next_version(paper.id, variant="enhanced") == 2


class TestStrategyRepositorySupersede:
    def test_supersede_previous(self, strat_repo, strat_session):
        paper = _make_paper(strat_session)
        old = strat_repo.create(_make_strategy_record(paper.id, version=1))
        new = strat_repo.create(_make_strategy_record(paper.id, version=2))

        strat_repo.supersede_previous(paper.id, variant="base", keep_id=new.id)

        old_fetched = strat_repo.get_by_id(old.id)
        new_fetched = strat_repo.get_by_id(new.id)
        assert old_fetched.status == StrategyStatus.SUPERSEDED.value
        assert new_fetched.status == StrategyStatus.DRAFT.value  # untouched

    def test_supersede_keeps_rejected(self, strat_repo, strat_session):
        """REJECTED records should not be re-superseded."""
        paper = _make_paper(strat_session)
        rejected = _make_strategy_record(paper.id, version=1, status=StrategyStatus.REJECTED)
        strat_repo.create(rejected)
        new = strat_repo.create(_make_strategy_record(paper.id, version=2))

        strat_repo.supersede_previous(paper.id, variant="base", keep_id=new.id)

        rejected_fetched = strat_repo.get_by_id(rejected.id)
        # Should remain REJECTED, not changed to SUPERSEDED
        assert rejected_fetched.status == StrategyStatus.REJECTED.value
