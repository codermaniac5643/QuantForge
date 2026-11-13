# tests/llm/test_evaluator.py

import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock

from quantforge.llm.evaluator import LLMEvaluator
from quantforge.llm.budget import BudgetManager, BudgetExhaustedError, LLMDisabledError
from quantforge.llm.provider import LLMResponse, LLMProviderError
from quantforge.models.paper import ResearchPaper
from quantforge.models.evaluation import PaperEvaluation, DimensionScore


def make_paper(**kwargs) -> ResearchPaper:
    defaults = {
        "title": "Cross-Sectional Momentum Strategies",
        "abstract": "We study momentum strategies across equity markets using daily data.",
        "keywords": ["momentum", "equity"],
    }
    defaults.update(kwargs)
    return ResearchPaper(**defaults)


def make_evaluation(paper: ResearchPaper) -> PaperEvaluation:
    return PaperEvaluation(
        paper_id=paper.id,
        relevance=DimensionScore(score=0.8, rationale="Relevant"),
        credibility=DimensionScore(score=0.7, rationale="Credible"),
    )


def make_llm_response(score: float = 0.75) -> LLMResponse:
    import json
    content = json.dumps({
        "score": score,
        "rationale": "Novel momentum approach with practical implementation path",
        "evidence": ["Uses daily data", "Out-of-sample tested"],
    })
    return LLMResponse(
        content=content,
        model="anthropic/claude-3-haiku",
        input_tokens=200,
        output_tokens=80,
        total_tokens=280,
    )


def make_budget(response: LLMResponse | None = None) -> BudgetManager:
    provider = MagicMock()
    provider.model = "anthropic/claude-3-haiku"
    provider.complete = AsyncMock(return_value=response or make_llm_response())
    return BudgetManager(
        provider=provider,
        max_spend_usd=5.0,
        warn_at_usd=3.0,
        enabled=True,
    )


# --- Priority Evaluation ---

@pytest.mark.asyncio
async def test_evaluate_priority_fills_score():
    paper = make_paper()
    evaluation = make_evaluation(paper)
    budget = make_budget(make_llm_response(score=0.75))
    evaluator = LLMEvaluator(budget)

    result = await evaluator.evaluate_priority(paper, evaluation)

    assert result.priority is not None
    assert result.priority.score == 0.75
    assert result.priority.llm_assisted is True
    assert result.priority.rationale is not None
    assert len(result.priority.evidence) == 2


@pytest.mark.asyncio
async def test_evaluate_priority_budget_exhausted_returns_unchanged():
    paper = make_paper()
    evaluation = make_evaluation(paper)

    budget = make_budget()
    budget.call = AsyncMock(side_effect=BudgetExhaustedError("exhausted"))
    evaluator = LLMEvaluator(budget)

    result = await evaluator.evaluate_priority(paper, evaluation)

    # Priority stays None — evaluation not failed
    assert result.priority is None
    assert result.relevance is not None   # other scores untouched


@pytest.mark.asyncio
async def test_evaluate_priority_llm_disabled_returns_unchanged():
    paper = make_paper()
    evaluation = make_evaluation(paper)

    budget = make_budget()
    budget.call = AsyncMock(side_effect=LLMDisabledError("disabled"))
    evaluator = LLMEvaluator(budget)

    result = await evaluator.evaluate_priority(paper, evaluation)
    assert result.priority is None


@pytest.mark.asyncio
async def test_evaluate_priority_bad_json_returns_unchanged():
    paper = make_paper()
    evaluation = make_evaluation(paper)

    bad_response = LLMResponse(
        content="this is not json at all",
        model="anthropic/claude-3-haiku",
        input_tokens=100,
        output_tokens=20,
        total_tokens=120,
    )
    budget = make_budget(bad_response)
    evaluator = LLMEvaluator(budget)

    result = await evaluator.evaluate_priority(paper, evaluation)
    assert result.priority is None


@pytest.mark.asyncio
async def test_evaluate_priority_provider_error_returns_unchanged():
    paper = make_paper()
    evaluation = make_evaluation(paper)

    budget = make_budget()
    budget.call = AsyncMock(side_effect=LLMProviderError("provider down"))
    evaluator = LLMEvaluator(budget)

    result = await evaluator.evaluate_priority(paper, evaluation)
    assert result.priority is None


# --- Batch Evaluation ---

@pytest.mark.asyncio
async def test_evaluate_batch_fills_all_priorities():
    papers = [make_paper(title=f"Paper {i}") for i in range(3)]
    evaluations = {str(p.id): make_evaluation(p) for p in papers}

    budget = make_budget(make_llm_response(score=0.8))
    evaluator = LLMEvaluator(budget)

    result = await evaluator.evaluate_batch(papers, evaluations)

    for paper in papers:
        assert result[str(paper.id)].priority is not None


@pytest.mark.asyncio
async def test_evaluate_batch_stops_on_budget_exhaustion():
    papers = [make_paper(title=f"Paper {i}") for i in range(5)]
    evaluations = {str(p.id): make_evaluation(p) for p in papers}

    budget = make_budget()
    call_count = 0

    async def mock_call(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count > 2:
            raise BudgetExhaustedError("exhausted")
        return make_llm_response()

    budget.call = mock_call
    evaluator = LLMEvaluator(budget)

    result = await evaluator.evaluate_batch(papers, evaluations)

    scored = sum(1 for e in result.values() if e.priority is not None)
    unscored = sum(1 for e in result.values() if e.priority is None)
    assert scored == 2
    assert unscored == 3


@pytest.mark.asyncio
async def test_evaluate_batch_skips_missing_evaluations():
    papers = [make_paper(title=f"Paper {i}") for i in range(3)]
    # Only include evaluation for first paper
    evaluations = {str(papers[0].id): make_evaluation(papers[0])}

    budget = make_budget(make_llm_response())
    evaluator = LLMEvaluator(budget)

    result = await evaluator.evaluate_batch(papers, evaluations)
    assert len(result) == 1