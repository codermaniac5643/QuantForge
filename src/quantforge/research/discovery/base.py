# src/quantforge/research/discovery/base.py

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from quantforge.models.paper import ResearchPaper


@dataclass
class SearchQuery:
    """
    A single search request passed to a connector.
    Connectors translate this into their own API format.
    """
    term: str                           # the search string
    topic_name: str                     # which topic this belongs to
    max_results: int = 50               # how many results to fetch
    lookback_days: int = 365            # how far back to search


@dataclass
class DiscoveryResult:
    """
    What a connector returns after a search.
    Papers are not yet persisted — just discovered and normalized.
    """
    source_name: str
    query: SearchQuery
    papers: list[ResearchPaper] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    total_found: int = 0                # total hits reported by the source
    total_fetched: int = 0              # how many we actually got back

    @property
    def success(self) -> bool:
        return len(self.errors) == 0


class BaseConnector(ABC):
    """
    Abstract base for all source connectors.

    Every connector must:
    - Accept a SearchQuery
    - Return a DiscoveryResult with normalized ResearchPaper objects
    - Never raise on recoverable errors — record them in DiscoveryResult.errors
    - Never own pipeline state or database access
    """

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Unique identifier for this source e.g. 'arxiv', 'openalex'."""
        ...

    @abstractmethod
    async def search(self, query: SearchQuery) -> DiscoveryResult:
        """
        Execute a search and return normalized results.
        Must handle rate limits, timeouts, and malformed responses gracefully.
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """Release any held resources e.g. HTTP client connections."""
        ...

    async def __aenter__(self) -> "BaseConnector":
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()