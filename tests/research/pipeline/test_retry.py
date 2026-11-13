# tests/research/pipeline/test_retry.py

import pytest
from unittest.mock import AsyncMock

from quantforge.research.pipeline.retry import (
    RetryPolicy,
    with_retry,
    DISCOVERY_RETRY,
    ACQUISITION_RETRY,
    NO_RETRY,
)
from quantforge.llm.budget import BudgetExhaustedError


# --- RetryPolicy ---

def test_permanent_error_not_retryable():
    policy = RetryPolicy(max_attempts=3)
    assert policy.is_permanent(BudgetExhaustedError("exhausted"))
    assert not policy.is_retryable(BudgetExhaustedError("exhausted"))


def test_generic_exception_is_retryable():
    policy = RetryPolicy(max_attempts=3)
    assert not policy.is_permanent(ConnectionError("timeout"))
    assert policy.is_retryable(ConnectionError("timeout"))


def test_value_error_is_permanent():
    policy = RetryPolicy(max_attempts=3)
    assert policy.is_permanent(ValueError("bad value"))


# --- with_retry ---

@pytest.mark.asyncio
async def test_success_on_first_attempt():
    fn = AsyncMock(return_value="ok")
    result = await with_retry(fn, NO_RETRY, label="test")
    assert result == "ok"
    fn.assert_called_once()


@pytest.mark.asyncio
async def test_retries_on_transient_error():
    fn = AsyncMock(side_effect=[ConnectionError("fail"), "ok"])
    policy = RetryPolicy(max_attempts=2, delay_seconds=0)
    result = await with_retry(fn, policy, label="test")
    assert result == "ok"
    assert fn.call_count == 2


@pytest.mark.asyncio
async def test_raises_after_max_attempts():
    fn = AsyncMock(side_effect=ConnectionError("always fails"))
    policy = RetryPolicy(max_attempts=3, delay_seconds=0)
    with pytest.raises(ConnectionError):
        await with_retry(fn, policy, label="test")
    assert fn.call_count == 3


@pytest.mark.asyncio
async def test_permanent_error_stops_immediately():
    fn = AsyncMock(side_effect=BudgetExhaustedError("exhausted"))
    policy = RetryPolicy(max_attempts=3, delay_seconds=0)
    with pytest.raises(BudgetExhaustedError):
        await with_retry(fn, policy, label="test")
    # Must not retry — called only once
    fn.assert_called_once()


@pytest.mark.asyncio
async def test_no_retry_policy_raises_immediately():
    fn = AsyncMock(side_effect=ConnectionError("fail"))
    with pytest.raises(ConnectionError):
        await with_retry(fn, NO_RETRY, label="test")
    fn.assert_called_once()


# --- Preset policies ---

def test_discovery_retry_allows_multiple_attempts():
    assert DISCOVERY_RETRY.max_attempts == 3


def test_acquisition_retry_allows_multiple_attempts():
    assert ACQUISITION_RETRY.max_attempts == 2


def test_no_retry_is_single_attempt():
    assert NO_RETRY.max_attempts == 1