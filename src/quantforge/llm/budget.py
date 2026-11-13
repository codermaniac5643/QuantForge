# src/quantforge/llm/budget.py

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock

from quantforge.llm.provider import LLMRequest, LLMResponse, LLMProvider

logger = logging.getLogger(__name__)


class BudgetExhaustedError(Exception):
    """Raised when a request would exceed the configured budget limit."""
    pass


class LLMDisabledError(Exception):
    """Raised when LLM usage is disabled in config."""
    pass


@dataclass
class CallRecord:
    """A log entry for a single LLM call."""
    timestamp: datetime
    task: str                   # e.g. "relevance_evaluation"
    paper_id: str | None
    input_tokens: int
    output_tokens: int
    cost_usd: float
    model: str
    success: bool
    error: str | None = None


@dataclass
class BudgetStatus:
    """Current state of the budget."""
    total_spend_usd: float
    max_spend_usd: float
    warn_at_usd: float
    total_calls: int
    failed_calls: int
    remaining_usd: float
    is_exhausted: bool
    is_warning: bool


class BudgetManager:
    """
    Tracks LLM spend and enforces hard budget limits.

    All LLM calls must go through call() — never call the provider directly.
    Thread-safe spend tracking via a lock.
    Budget exhaustion is a controlled stop condition, not a crash.
    """

    def __init__(
        self,
        provider: LLMProvider,
        max_spend_usd: float,
        warn_at_usd: float,
        enabled: bool = True,
        max_calls_per_paper: int = 3,
    ) -> None:
        self._provider = provider
        self._max_spend_usd = max_spend_usd
        self._warn_at_usd = warn_at_usd
        self._enabled = enabled
        self._max_calls_per_paper = max_calls_per_paper

        self._total_spend_usd: float = 0.0
        self._call_log: list[CallRecord] = []
        self._calls_per_paper: dict[str, int] = {}
        self._lock = Lock()

    @property
    def total_spend_usd(self) -> float:
        return self._total_spend_usd

    @property
    def is_exhausted(self) -> bool:
        return self._total_spend_usd >= self._max_spend_usd

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def status(self) -> BudgetStatus:
        return BudgetStatus(
            total_spend_usd=round(self._total_spend_usd, 6),
            max_spend_usd=self._max_spend_usd,
            warn_at_usd=self._warn_at_usd,
            total_calls=len(self._call_log),
            failed_calls=sum(1 for c in self._call_log if not c.success),
            remaining_usd=round(
                max(0.0, self._max_spend_usd - self._total_spend_usd), 6
            ),
            is_exhausted=self.is_exhausted,
            is_warning=self._total_spend_usd >= self._warn_at_usd,
        )

    def _check_paper_call_limit(self, paper_id: str) -> None:
        """Raise if this paper has already hit its per-paper call cap."""
        count = self._calls_per_paper.get(paper_id, 0)
        if count >= self._max_calls_per_paper:
            raise BudgetExhaustedError(
                f"Paper {paper_id!r} has reached the per-paper call limit "
                f"of {self._max_calls_per_paper}"
            )

    async def call(
        self,
        request: LLMRequest,
        task: str,
        paper_id: str | None = None,
    ) -> LLMResponse:
        """
        Execute an LLM call through the budget manager.
        Checks budget before calling, records spend after.

        Args:
            request: The LLM request to execute.
            task: Which pipeline task is making this call e.g. 'relevance_evaluation'.
            paper_id: The paper this call is about — used for per-paper call limiting.

        Raises:
            LLMDisabledError: If LLM usage is disabled in config.
            BudgetExhaustedError: If the budget limit has been reached.
            LLMProviderError: If the provider returns an unrecoverable error.
        """
        if not self._enabled:
            raise LLMDisabledError("LLM usage is disabled in config (llm.enabled: false)")

        with self._lock:
            if self.is_exhausted:
                raise BudgetExhaustedError(
                    f"Budget exhausted: ${self._total_spend_usd:.4f} / "
                    f"${self._max_spend_usd:.4f} spent"
                )
            if paper_id:
                self._check_paper_call_limit(paper_id)

        # Warn if approaching budget limit
        status = self.status()
        if status.is_warning and not status.is_exhausted:
            logger.warning(
                "LLM budget warning: $%.4f / $%.4f spent (%.0f%%)",
                self._total_spend_usd,
                self._max_spend_usd,
                (self._total_spend_usd / self._max_spend_usd) * 100,
            )

        # Make the actual call
        try:
            response = await self._provider.complete(request)

            with self._lock:
                cost = response.estimated_cost_usd
                self._total_spend_usd += cost
                if paper_id:
                    self._calls_per_paper[paper_id] = (
                        self._calls_per_paper.get(paper_id, 0) + 1
                    )
                self._call_log.append(CallRecord(
                    timestamp=datetime.now(timezone.utc),
                    task=task,
                    paper_id=paper_id,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    cost_usd=cost,
                    model=response.model,
                    success=True,
                ))

            logger.info(
                "LLM call [%s] paper=%s — %d tokens, $%.6f (total: $%.4f)",
                task,
                paper_id or "none",
                response.total_tokens,
                cost,
                self._total_spend_usd,
            )
            return response

        except Exception as e:
            with self._lock:
                self._call_log.append(CallRecord(
                    timestamp=datetime.now(timezone.utc),
                    task=task,
                    paper_id=paper_id,
                    input_tokens=0,
                    output_tokens=0,
                    cost_usd=0.0,
                    model=self._provider.model,
                    success=False,
                    error=str(e),
                ))
            raise

    def call_log(self) -> list[CallRecord]:
        """Return a copy of the full call log."""
        return list(self._call_log)