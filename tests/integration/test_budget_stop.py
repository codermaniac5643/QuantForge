# tests/integration/test_budget_stop.py

"""
Integration tests: LLM budget exhaustion stops gracefully.
No real LLM calls — budget is set to zero or exhausted via mocks.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from quantforge.llm.budget import BudgetManager, BudgetExhaustedError
from quantforge.llm.provider import LLMResponse
from quantforge.models.evaluation import PaperEvaluation
from quantforge.models.states import PaperState
from quantforge.research.pipeline.stages import run_llm_stage
from tests.integration.conftest import make_paper_record


def make_llm_response(score: float = 0.75) -> LLMResponse:
    import json
    return LLMResponse(
        content=json.dumps({
            "score": score,
            "rationale": "Strong momentum research",
            "evidence": ["Uses daily data", "Explicit trading rules"],
        }),
        model="anthropic/claude-3-haiku",
        input_tokens=200,
        output_tokens=80,
        total_tokens=280,
    )


def make_budget_manager(
    max_spend_usd: float = 5.0,
    enabled: bool = True,
    response: LLMResponse | None = None,
) -> BudgetManager:
    provider = MagicMock()
    provider.model = "anthropic/claude-3-haiku"
    provider.complete = AsyncMock(return_value=response or make_llm_response())
    return BudgetManager(
        provider=provider,
        max_spend_usd=max_spend_usd,
        warn_at_usd=max_spend_usd * 0.6,
        enabled=enabled,
        max_calls_per_paper=3,
    )


# --- Budget Exhaustion ---

@pytest.mark.asyncio
async def test_zero_budget_raises_before_call(int_repo, pipeline_ctx):
    """A budget of $0 prevents any LLM call from being made."""
    budget = make_budget_manager(max_spend_usd=0.0)
    pipeline_ctx.budget_manager = budget

    record = make_paper_record(state=PaperState.CANDIDATE)
    int_repo.create(record)

    with pytest.raises(BudgetExhaustedError):
        await budget.call(
            request=MagicMock(),
            task="test",
            paper_id=str(record.id),
        )


@pytest.mark.asyncio
async def test_llm_stage_handles_zero_budget_gracefully(int_repo, pipeline_ctx):
    """LLM stage returns success=True when budget is zero — evaluation continues."""
    budget = make_budget_manager(max_spend_usd=0.0)
    pipeline_ctx.budget_manager = budget

    record = make_paper_record(state=PaperState.CANDIDATE)
    int_repo.create(record)

    result = await run_llm_stage(record.id, pipeline_ctx)

    # Stage succeeds — budget exhaustion is not a pipeline failure
    assert result.success
    assert result.skipped

    # Paper still in CANDIDATE state — not rejected
    updated = int_repo.get_by_id(record.id)
    assert PaperState(updated.state) == PaperState.CANDIDATE


@pytest.mark.asyncio
async def test_budget_exhausted_mid_batch(int_repo, pipeline_ctx):
    """
    Budget exhaustion mid-batch stops LLM calls for remaining papers
    but does not fail or reject them.
    """
    from quantforge.llm.evaluator import LLMEvaluator

    # Only enough budget for ~1 call
    budget = make_budget_manager(max_spend_usd=0.000001)
    pipeline_ctx.budget_manager = budget

    records = [
        make_paper_record(state=PaperState.CANDIDATE, title=f"Paper {i}")
        for i in range(5)
    ]
    for r in records:
        int_repo.create(r)

    from quantforge.models.paper import ResearchPaper, Author
    papers = []
    evaluations = {}
    for r in records:
        paper = ResearchPaper(
            id=r.id,
            title=r.title,
            abstract="We study momentum strategies.",
            keywords=["momentum"],
        )
        papers.append(paper)
        evaluations[str(r.id)] = PaperEvaluation(paper_id=r.id)

    evaluator = LLMEvaluator(budget)
    result = await evaluator.evaluate_batch(papers, evaluations)

    # All evaluations returned — none dropped
    assert len(result) == 5

    # Budget is exhausted
    assert budget.is_exhausted


@pytest.mark.asyncio
async def test_per_paper_call_limit_enforced(int_repo, pipeline_ctx):
    """A single paper cannot exceed its per-paper call limit."""
    budget = make_budget_manager(max_spend_usd=10.0)
    budget._max_calls_per_paper = 2

    record = make_paper_record(state=PaperState.CANDIDATE)
    int_repo.create(record)
    paper_id = str(record.id)

    # First two calls succeed
    from quantforge.llm.provider import LLMRequest
    req = LLMRequest(prompt="evaluate this paper")
    await budget.call(req, task="test", paper_id=paper_id)
    await budget.call(req, task="test", paper_id=paper_id)

    # Third call hits per-paper limit
    with pytest.raises(BudgetExhaustedError, match="per-paper"):
        await budget.call(req, task="test", paper_id=paper_id)


@pytest.mark.asyncio
async def test_budget_call_log_records_all_attempts(int_repo, pipeline_ctx):
    """Every call attempt is recorded in the call log."""
    budget = make_budget_manager(max_spend_usd=10.0)

    from quantforge.llm.provider import LLMRequest
    req = LLMRequest(prompt="test")
    record = make_paper_record(state=PaperState.CANDIDATE)
    int_repo.create(record)

    await budget.call(req, task="priority_evaluation", paper_id=str(record.id))
    await budget.call(req, task="priority_evaluation", paper_id=str(record.id))

    log = budget.call_log()
    assert len(log) == 2
    assert all(entry.success for entry in log)
    assert all(entry.task == "priority_evaluation" for entry in log)


@pytest.mark.asyncio
async def test_disabled_llm_skips_all_calls(int_repo, pipeline_ctx):
    """When LLM is disabled, no calls are made and stages skip cleanly."""
    from quantforge.llm.budget import LLMDisabledError

    budget = make_budget_manager(enabled=False)
    pipeline_ctx.budget_manager = budget

    record = make_paper_record(state=PaperState.CANDIDATE)
    int_repo.create(record)

    # Direct call raises LLMDisabledError
    from quantforge.llm.provider import LLMRequest
    with pytest.raises(LLMDisabledError):
        await budget.call(
            LLMRequest(prompt="test"),
            task="test",
        )

    # Stage handles it gracefully
    result = await run_llm_stage(record.id, pipeline_ctx)
    assert result.success
    assert result.skipped