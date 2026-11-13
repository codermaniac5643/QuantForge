# tests/llm/test_budget.py

import pytest
from unittest.mock import AsyncMock, MagicMock

from quantforge.llm.budget import (
    BudgetManager,
    BudgetExhaustedError,
    LLMDisabledError,
)
from quantforge.llm.provider import LLMRequest, LLMResponse, LLMProviderError


def make_response(input_tokens: int = 100, output_tokens: int = 50) -> LLMResponse:
    return LLMResponse(
        content="test response",
        model="anthropic/claude-3-haiku",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
    )


def make_provider(response: LLMResponse | None = None) -> MagicMock:
    provider = MagicMock()
    provider.model = "anthropic/claude-3-haiku"
    provider.complete = AsyncMock(return_value=response or make_response())
    return provider


def make_budget(
    provider=None,
    max_spend_usd: float = 5.0,
    warn_at_usd: float = 3.0,
    enabled: bool = True,
    max_calls_per_paper: int = 3,
) -> BudgetManager:
    return BudgetManager(
        provider=provider or make_provider(),
        max_spend_usd=max_spend_usd,
        warn_at_usd=warn_at_usd,
        enabled=enabled,
        max_calls_per_paper=max_calls_per_paper,
    )


# --- Basic Call ---

@pytest.mark.asyncio
async def test_successful_call_returns_response():
    budget = make_budget()
    response = await budget.call(
        LLMRequest(prompt="test"), task="test_task"
    )
    assert response.content == "test response"


@pytest.mark.asyncio
async def test_successful_call_records_spend():
    response = make_response(input_tokens=1_000_000, output_tokens=0)
    budget = make_budget(provider=make_provider(response))
    await budget.call(LLMRequest(prompt="test"), task="test_task")
    # Input only: $0.25 per 1M tokens
    assert budget.total_spend_usd > 0.0


@pytest.mark.asyncio
async def test_call_logged_on_success():
    budget = make_budget()
    await budget.call(
        LLMRequest(prompt="test"), task="priority_evaluation", paper_id="paper-1"
    )
    log = budget.call_log()
    assert len(log) == 1
    assert log[0].task == "priority_evaluation"
    assert log[0].paper_id == "paper-1"
    assert log[0].success is True


@pytest.mark.asyncio
async def test_call_logged_on_failure():
    provider = make_provider()
    provider.complete = AsyncMock(side_effect=LLMProviderError("boom"))
    budget = make_budget(provider=provider)

    with pytest.raises(LLMProviderError):
        await budget.call(LLMRequest(prompt="test"), task="test_task")

    log = budget.call_log()
    assert len(log) == 1
    assert log[0].success is False
    assert "boom" in log[0].error


# --- Disabled ---

@pytest.mark.asyncio
async def test_disabled_raises_llm_disabled_error():
    budget = make_budget(enabled=False)
    with pytest.raises(LLMDisabledError):
        await budget.call(LLMRequest(prompt="test"), task="test_task")


# --- Budget Exhaustion ---

@pytest.mark.asyncio
async def test_budget_exhausted_raises_before_call():
    budget = make_budget(max_spend_usd=0.0)
    with pytest.raises(BudgetExhaustedError):
        await budget.call(LLMRequest(prompt="test"), task="test_task")


@pytest.mark.asyncio
async def test_budget_exhausted_after_spend():
    response = make_response(input_tokens=1_000_000, output_tokens=1_000_000)
    budget = make_budget(
        provider=make_provider(response),
        max_spend_usd=0.000001,   # tiny limit
    )
    # First call spends over the limit
    try:
        await budget.call(LLMRequest(prompt="test"), task="test_task")
    except BudgetExhaustedError:
        pass

    assert budget.is_exhausted


# --- Per-Paper Call Limit ---

@pytest.mark.asyncio
async def test_per_paper_call_limit_enforced():
    budget = make_budget(max_calls_per_paper=2)

    await budget.call(LLMRequest(prompt="test"), task="t", paper_id="p1")
    await budget.call(LLMRequest(prompt="test"), task="t", paper_id="p1")

    with pytest.raises(BudgetExhaustedError, match="per-paper"):
        await budget.call(LLMRequest(prompt="test"), task="t", paper_id="p1")


@pytest.mark.asyncio
async def test_per_paper_limit_independent_per_paper():
    budget = make_budget(max_calls_per_paper=1)

    await budget.call(LLMRequest(prompt="test"), task="t", paper_id="p1")
    # Different paper — should not be limited
    await budget.call(LLMRequest(prompt="test"), task="t", paper_id="p2")


# --- Status ---

@pytest.mark.asyncio
async def test_status_reflects_spend():
    budget = make_budget(max_spend_usd=5.0, warn_at_usd=3.0)
    await budget.call(LLMRequest(prompt="test"), task="test_task")

    status = budget.status()
    assert status.total_spend_usd >= 0.0
    assert status.max_spend_usd == 5.0
    assert status.remaining_usd <= 5.0
    assert status.total_calls == 1
    assert status.failed_calls == 0


def test_initial_status():
    budget = make_budget(max_spend_usd=5.0, warn_at_usd=3.0)
    status = budget.status()
    assert status.total_spend_usd == 0.0
    assert status.remaining_usd == 5.0
    assert status.is_exhausted is False
    assert status.is_warning is False
    assert status.total_calls == 0