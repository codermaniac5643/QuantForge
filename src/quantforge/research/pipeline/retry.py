# src/quantforge/research/pipeline/retry.py

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable, Awaitable, TypeVar

from quantforge.llm.budget import BudgetExhaustedError, LLMDisabledError
from quantforge.research.acquisition.validator import ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Errors that should never be retried — retrying won't change the outcome
PERMANENT_ERRORS = (
    BudgetExhaustedError,
    LLMDisabledError,
    ValidationError,
    ValueError,
    FileNotFoundError,
)


@dataclass
class RetryPolicy:
    """
    Defines how a stage handles transient failures.

    max_attempts: total attempts including the first (1 = no retry)
    delay_seconds: wait between attempts
    retryable_exceptions: which exceptions trigger a retry
                          None means retry on any non-permanent exception
    """
    max_attempts: int = 3
    delay_seconds: float = 2.0
    retryable_exceptions: tuple | None = None

    def is_permanent(self, exc: Exception) -> bool:
        """Returns True if this error should not be retried."""
        return isinstance(exc, PERMANENT_ERRORS)

    def is_retryable(self, exc: Exception) -> bool:
        """Returns True if this error warrants a retry."""
        if self.is_permanent(exc):
            return False
        if self.retryable_exceptions is not None:
            return isinstance(exc, self.retryable_exceptions)
        return True


# --- Preset policies per stage ---

DISCOVERY_RETRY = RetryPolicy(
    max_attempts=3,
    delay_seconds=3.0,
)

ACQUISITION_RETRY = RetryPolicy(
    max_attempts=2,
    delay_seconds=5.0,
)

PROCESSING_RETRY = RetryPolicy(
    max_attempts=2,
    delay_seconds=1.0,
)

LLM_RETRY = RetryPolicy(
    max_attempts=1,     # budget manager handles its own retries
    delay_seconds=0.0,
)

NO_RETRY = RetryPolicy(
    max_attempts=1,
    delay_seconds=0.0,
)


async def with_retry(
    fn: Callable[[], Awaitable[T]],
    policy: RetryPolicy,
    label: str = "",
) -> T:
    """
    Execute an async callable with retry logic.
    Stops immediately on permanent errors.
    Logs each retry attempt with the error and wait time.

    Args:
        fn: async callable to execute
        policy: retry policy to apply
        label: descriptive label for log messages

    Returns:
        Result of fn() on success

    Raises:
        The last exception if all attempts are exhausted
    """
    last_exc: Exception | None = None

    for attempt in range(1, policy.max_attempts + 1):
        try:
            return await fn()
        except Exception as exc:
            last_exc = exc

            if policy.is_permanent(exc):
                logger.debug(
                    "%s — permanent error, not retrying: %s",
                    label, exc,
                )
                raise

            if attempt == policy.max_attempts:
                logger.warning(
                    "%s — failed after %d attempt(s): %s",
                    label, attempt, exc,
                )
                raise

            if policy.is_retryable(exc):
                logger.warning(
                    "%s — attempt %d/%d failed: %s. Retrying in %.1fs...",
                    label, attempt, policy.max_attempts,
                    exc, policy.delay_seconds,
                )
                await asyncio.sleep(policy.delay_seconds)
            else:
                raise
    # Safety net: if the loop exits without returning or raising (shouldn't
    # happen given the logic above, but satisfies the type-checker and guards
    # against future refactors).
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("with_retry: loop exited without result or exception")