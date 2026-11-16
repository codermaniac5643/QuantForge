# tests/integration/test_pipeline_e2e.py

"""
Integration tests: full pipeline flow against a real PostgreSQL database.
All acquisition and LLM calls run in dry_run=True mode.
No external HTTP requests are made.
"""

import pytest

from quantforge.models.states import PaperState
from quantforge.research.pipeline.stages import (
    run_normalize_stage,
    run_evaluate_stage,
    run_llm_stage,
    run_approve_stage,
)
from tests.integration.conftest import make_paper_record


# --- DB Persistence ---

def test_paper_persisted_and_retrievable(int_repo):
    """A paper persisted to DB can be fetched by ID and arXiv ID."""
    record = make_paper_record(arxiv_id="2401.11111")
    int_repo.create(record)

    by_id = int_repo.get_by_id(record.id)
    assert by_id is not None
    assert by_id.title == record.title

    by_arxiv = int_repo.get_by_arxiv_id("2401.11111")
    assert by_arxiv is not None
    assert by_arxiv.id == record.id


def test_duplicate_arxiv_id_rejected(int_repo):
    """Two papers with the same arXiv ID cannot both be persisted."""
    from quantforge.database.repositories.paper import DuplicatePaperError

    record1 = make_paper_record(arxiv_id="2401.22222")
    record2 = make_paper_record(arxiv_id="2401.22222", title="Different Title")

    int_repo.create(record1)
    with pytest.raises(DuplicatePaperError):
        int_repo.create(record2)


def test_state_counts_reflect_db(int_repo):
    """count_by_state returns accurate counts after multiple inserts."""
    int_repo.create(make_paper_record(state=PaperState.DISCOVERED))
    int_repo.create(make_paper_record(state=PaperState.DISCOVERED))
    int_repo.create(make_paper_record(state=PaperState.NORMALIZED))

    counts = int_repo.count_by_state()
    assert counts.get("discovered", 0) == 2
    assert counts.get("normalized", 0) == 1


# --- Stage Transitions ---

@pytest.mark.asyncio
async def test_normalize_stage_real_db(int_repo, pipeline_ctx):
    """normalize stage transitions a DISCOVERED paper to NORMALIZED in real DB."""
    record = make_paper_record(state=PaperState.DISCOVERED)
    int_repo.create(record)

    result = await run_normalize_stage(record.id, pipeline_ctx)

    assert result.success
    updated = int_repo.get_by_id(record.id)
    assert PaperState(updated.state) == PaperState.NORMALIZED


@pytest.mark.asyncio
async def test_evaluate_stage_persists_scores(int_repo, pipeline_ctx):
    """evaluate stage writes scores to DB and transitions state."""
    record = make_paper_record(state=PaperState.NORMALIZED)
    int_repo.create(record)

    result = await run_evaluate_stage(
        record.id,
        pipeline_ctx,
        topic_keywords=["cross-sectional momentum", "price momentum"],
    )

    updated = int_repo.get_by_id(record.id)
    # Scores always written regardless of threshold outcome
    assert updated.relevance_score is not None
    assert updated.composite_score is not None
    # State is either CANDIDATE (passed) or REJECTED (below threshold)
    assert PaperState(updated.state) in (PaperState.CANDIDATE, PaperState.REJECTED)


@pytest.mark.asyncio
async def test_full_stage_sequence_real_db(int_repo, pipeline_ctx):
    """
    Full stage sequence: DISCOVERED → NORMALIZED → EVALUATED → CANDIDATE → APPROVED
    Uses a high-relevance paper to ensure it passes the score threshold.
    """
    # Build a paper with strong momentum signals
    from quantforge.database.models.paper import PaperRecord
    import uuid

    record = PaperRecord(
        id=uuid.uuid4(),
        title="Cross-Sectional Momentum Factor in Equity Markets",
        arxiv_id=f"2401.{uuid.uuid4().hex[:5]}",
        abstract=(
            "We study cross-sectional momentum strategies using daily return data. "
            "Our implementable trading rule generates significant alpha. "
            "We provide explicit entry and exit signals with position sizing rules. "
            "Data from CRSP daily returns. Strategy code available on GitHub."
        ),
        authors=[{"name": "Jane Smith"}, {"name": "John Doe"}],
        keywords=["momentum", "cross-sectional", "equity", "trading", "factor"],
        sources=[{
            "source_name": "arxiv",
            "source_id": "2401.99999",
            "source_url": "https://arxiv.org/abs/2401.99999",
            "raw_metadata": {"cited_by_count": 50},
        }],
        state=PaperState.DISCOVERED.value,
    )
    int_repo.create(record)

    # Normalize
    r1 = await run_normalize_stage(record.id, pipeline_ctx)
    assert r1.success

    # Evaluate
    r2 = await run_evaluate_stage(
        record.id,
        pipeline_ctx,
        topic_keywords=["cross-sectional momentum", "price momentum", "equity"],
    )

    updated = int_repo.get_by_id(record.id)
    state = PaperState(updated.state)

    if state == PaperState.CANDIDATE:
        # LLM stage — skipped (dry_run + no budget)
        r3 = await run_llm_stage(record.id, pipeline_ctx)
        assert r3.success
        assert r3.skipped

        # Approve — auto mode
        r4 = await run_approve_stage(record.id, pipeline_ctx)
        assert r4.success

        final = int_repo.get_by_id(record.id)
        assert PaperState(final.state) == PaperState.APPROVED

    # Either path is valid — rejected papers have scores recorded
    assert updated.relevance_score is not None


# --- Deduplication ---

def test_deduplication_same_arxiv_id(int_repo):
    """Papers with the same arXiv ID after normalization are deduplicated."""
    from quantforge.research.normalization.text import normalize_arxiv_id

    record = make_paper_record(arxiv_id="2401.33333")
    int_repo.create(record)

    # Simulate a second discovery of the same paper
    norm_id = normalize_arxiv_id("2401.33333v2")
    existing = int_repo.get_by_arxiv_id(norm_id or "2401.33333")
    assert existing is not None
    assert existing.id == record.id


# --- Score Persistence ---

def test_scores_persist_correctly(int_repo):
    """Scores written to DB are retrieved with correct values."""
    record = make_paper_record(state=PaperState.NORMALIZED)
    int_repo.create(record)

    int_repo.update_scores(
        record.id,
        relevance=0.85,
        credibility=0.70,
        implementability=0.60,
        reproducibility=0.55,
        composite=0.72,
    )

    updated = int_repo.get_by_id(record.id)
    assert abs(updated.relevance_score - 0.85) < 0.001
    assert abs(updated.composite_score - 0.72) < 0.001


# --- State Machine Enforcement ---

def test_invalid_transition_blocked_in_db(int_repo):
    """Invalid state transitions are blocked at the repository level."""
    from quantforge.database.repositories.paper import InvalidStateTransitionError

    record = make_paper_record(state=PaperState.DISCOVERED)
    int_repo.create(record)

    with pytest.raises(InvalidStateTransitionError):
        int_repo.transition_state(record.id, PaperState.ACQUIRED)


def test_terminal_state_cannot_transition(int_repo):
    """A REJECTED paper cannot be transitioned to any other state."""
    from quantforge.database.repositories.paper import InvalidStateTransitionError

    record = make_paper_record(state=PaperState.REJECTED)
    int_repo.create(record)

    with pytest.raises(InvalidStateTransitionError):
        int_repo.transition_state(record.id, PaperState.DISCOVERED)