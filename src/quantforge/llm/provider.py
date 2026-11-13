# src/quantforge/llm/provider.py

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class LLMRequest:
    prompt: str
    system_prompt: str | None = None
    max_tokens: int = 1000
    temperature: float = 0.1


@dataclass
class LLMResponse:
    content: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int

    @property
    def estimated_cost_usd(self) -> float:
        """
        Override in subclass or provider for accurate pricing.
        Default uses Claude Haiku via OpenRouter pricing.
        """
        input_cost = (self.input_tokens / 1_000_000) * 0.25
        output_cost = (self.output_tokens / 1_000_000) * 1.25
        return round(input_cost + output_cost, 6)


class LLMProviderError(Exception):
    """Raised when the LLM provider returns an unrecoverable error."""
    pass


class LLMProvider(ABC):
    """
    Abstract base for all LLM providers.
    Budget manager and evaluator talk to this — never to a concrete provider.
    Adding a new provider means implementing this interface, nothing else.
    """

    @property
    @abstractmethod
    def model(self) -> str:
        """The model identifier being used."""
        ...

    @abstractmethod
    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Execute a completion request and return the response."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Release any held resources."""
        ...

    async def __aenter__(self) -> "LLMProvider":
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()