# tests/integration/test_resume.py

"""
Integration tests: pipeline resume from last successful state.
Verifies that rerunning stages on already-progressed papers
skips completed work and does not duplicate or corrupt state.
"""

import pytest

from quantforge.models.states import PaperState
from quantforge.research.pipeline.stages import (
    run_normalize_stage,
    run_evaluate_stage,
    run_approve_stage,
    run_llm_stage,
)
from tests.integration.conftest import make_paper_record


# --- Resume from NORMALIZED ---

@pytest.mark.asyncio
async def test_normalize_skips_already_normalized(int_repo, pipeline_ctx):
    """Running normalize on an already-NORMALIZED paper skips and succeeds."""
    record = make_paper_record(state=PaperState.NORMALIZED)
    int_repo.create(record)

    result = await run_normalize_stage(record.id, pipeline_ctx)

    assert result.success
    assert result.skipped
    # State unchanged
    updated = int_repo.get_by_id(record.id)
    assert PaperState(updated.state) == PaperState.NORMALIZED


@pytest.mark.asyncio
async def test_normalize_skips_candidate(int_repo, pipeline_ctx):
    """Running normalize on a CANDIDATE paper skips — already past this stage."""
    record = make_paper_record(state=PaperState.CANDIDATE)
    int_repo.create(record)

    result = await run_normalize_stage(record.id, pipeline_ctx)

    assert result.success
    assert result.skipped
    updated = int_repo.get_by_id(record.id)
    assert PaperState(updated.state) == PaperState.CANDIDATE


# --- Resume from EVALUATED ---

@pytest.mark.asyncio
async def test_evaluate_skips_already_candidate(int_repo, pipeline_ctx):
    """Running evaluate on a CANDIDATE paper skips — already past this stage."""
    record = make_paper_record(state=PaperState.CANDIDATE)
    int_repo.create(record)

    result = await run_evaluate_stage(
        record.id, pipeline_ctx,
        topic_keywords=["momentum"],
    )

    assert result.success
    assert result.skipped
    updated = int_repo.get_by_id(record.id)
    assert PaperState(updated.state) == PaperState.CANDIDATE


@pytest.mark.asyncio
async def test_evaluate_skips_approved(int_repo, pipeline_ctx):
    """Running evaluate on an APPROVED paper skips."""
    record = make_paper_record(state=PaperState.APPROVED)
    int_repo.create(record)

    result = await run_evaluate_stage(
        record.id, pipeline_ctx,
        topic_keywords=["momentum"],
    )

    assert result.success
    assert result.skipped


# --- Resume from APPROVED ---

@pytest.mark.asyncio
async def test_approve_skips_already_approved(int_repo, pipeline_ctx):
    """Running approve on an already-APPROVED paper skips."""
    record = make_paper_record(state=PaperState.APPROVED)
    int_repo.create(record)

    pipeline_ctx.pipeline_config.acquisition.approval_mode = "auto"
    result = await run_approve_stage(record.id, pipeline_ctx)

    assert result.success
    assert result.skipped
    updated = int_repo.get_by_id(record.id)
    assert PaperState(updated.state) == PaperState.APPROVED


# --- Full resume sequence ---

@pytest.mark.asyncio
async def test_full_resume_from_normalized(int_repo, pipeline_ctx):
    """
    A paper already at NORMALIZED resumes from that point.
    Normalize is skipped, evaluate runs from NORMALIZED.
    """
    record = make_paper_record(state=PaperState.NORMALIZED)
    int_repo.create(record)

    # Normalize — should skip
    r1 = await run_normalize_stage(record.id, pipeline_ctx)
    assert r1.success
    assert r1.skipped

    # Evaluate — should run (paper may become CANDIDATE or REJECTED based on score)
    r2 = await run_evaluate_stage(
        record.id, pipeline_ctx,
        topic_keywords=["cross-sectional momentum", "price momentum"],
    )
    # Stage itself must complete without an unexpected error —
    # rejection due to a low score is a valid, successful outcome.
    assert r2.success or r2.error is not None and "below threshold" in r2.error

    updated = int_repo.get_by_id(record.id)
    assert PaperState(updated.state) in (PaperState.CANDIDATE, PaperState.REJECTED)
    assert updated.composite_score is not None


@pytest.mark.asyncio
async def test_idempotent_stage_run_does_not_duplicate(int_repo, pipeline_ctx):
    """
    Running the same stage twice on the same paper produces
    exactly one state transition — not two.
    """
    record = make_paper_record(state=PaperState.DISCOVERED)
    int_repo.create(record)

    # First run — transitions DISCOVERED → NORMALIZED
    r1 = await run_normalize_stage(record.id, pipeline_ctx)
    assert r1.success
    assert not r1.skipped

    # Second run — skips, state unchanged
    r2 = await run_normalize_stage(record.id, pipeline_ctx)
    assert r2.success
    assert r2.skipped

    updated = int_repo.get_by_id(record.id)
    assert PaperState(updated.state) == PaperState.NORMALIZED


@pytest.mark.asyncio
async def test_rejected_paper_skips_all_subsequent_stages(int_repo, pipeline_ctx):
    """
    A REJECTED paper is in a terminal state.
    All stages skip for it without error.
    """
    record = make_paper_record(state=PaperState.REJECTED)
    int_repo.create(record)

    r1 = await run_normalize_stage(record.id, pipeline_ctx)
    r2 = await run_evaluate_stage(
        record.id, pipeline_ctx, topic_keywords=["momentum"]
    )
    r3 = await run_llm_stage(record.id, pipeline_ctx)
    r4 = await run_approve_stage(record.id, pipeline_ctx)

    # All skip — none fail
    assert all(r.success for r in [r1, r2, r3, r4])
    # State unchanged — still REJECTED
    updated = int_repo.get_by_id(record.id)
    assert PaperState(updated.state) == PaperState.REJECTED


@pytest.mark.asyncio
async def test_scores_not_overwritten_on_resume(int_repo, pipeline_ctx):
    """
    Scores written in a previous run are preserved when
    a paper is already past the evaluate stage.
    """
    record = make_paper_record(state=PaperState.CANDIDATE)
    int_repo.create(record)

    # Write scores manually as if a previous run did it
    int_repo.update_scores(
        record.id,
        relevance=0.90,
        composite=0.85,
    )

    # Run evaluate — should skip because paper is already CANDIDATE
    result = await run_evaluate_stage(
        record.id, pipeline_ctx,
        topic_keywords=["momentum"],
    )
    assert result.skipped

    # Scores from previous run preserved
    updated = int_repo.get_by_id(record.id)
    assert abs(updated.relevance_score - 0.90) < 0.001
    assert abs(updated.composite_score - 0.85) < 0.001