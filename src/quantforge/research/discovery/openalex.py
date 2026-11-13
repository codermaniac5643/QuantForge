# src/quantforge/research/discovery/openalex.py

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx

from quantforge.models.paper import ResearchPaper, SourceReference, Author
from quantforge.models.states import PaperState
from quantforge.research.discovery.base import (
    BaseConnector,
    SearchQuery,
    DiscoveryResult,
)

logger = logging.getLogger(__name__)

OPENALEX_API_URL = "https://api.openalex.org/works"


class OpenAlexConnector(BaseConnector):
    """
    Discovers research papers from OpenAlex using their public API.
    Returns JSON — no authentication required.
    Polite pool: include a mailto email in requests for higher rate limits.
    Rate limit: 10 requests per second (polite pool), 1 per second otherwise.
    """

    def __init__(
        self,
        mailto: str | None = None,
        request_delay: float = 1.0,
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        self._mailto = mailto
        self._delay = request_delay
        self._timeout = timeout
        self._max_retries = max_retries
        self._client: httpx.AsyncClient | None = None

    @property
    def source_name(self) -> str:
        return "openalex"

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                headers={"User-Agent": "QuantForge/0.1 (research pipeline)"},
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def search(self, query: SearchQuery) -> DiscoveryResult:
        result = DiscoveryResult(
            source_name=self.source_name,
            query=query,
        )

        cutoff = datetime.now(timezone.utc) - timedelta(days=query.lookback_days)
        from_date = cutoff.strftime("%Y-%m-%d")

        params: dict[str, Any] = {
            "search": query.term,
            "filter": f"from_publication_date:{from_date}",
            "per_page": min(query.max_results, 200),   # OpenAlex max is 200
            "sort": "publication_date:desc",
            # Only fetch the fields we actually use — keeps responses small
            "select": ",".join([
                "id", "doi", "title", "abstract_inverted_index",
                "authorships", "publication_date", "primary_location",
                "keywords", "cited_by_count", "open_access",
            ]),
        }

        # Join the polite pool for higher rate limits
        if self._mailto:
            params["mailto"] = self._mailto

        for attempt in range(1, self._max_retries + 1):
            try:
                await asyncio.sleep(self._delay if attempt > 1 else 0)
                client = self._get_client()
                response = await client.get(OPENALEX_API_URL, params=params)
                response.raise_for_status()

                data = response.json()
                papers = self._parse_response(data, query)
                total = data.get("meta", {}).get("count", 0)

                result.papers = papers
                result.total_found = total
                result.total_fetched = len(papers)

                logger.info(
                    "OpenAlex search %r — fetched %d / %d results",
                    query.term,
                    result.total_fetched,
                    result.total_found,
                )
                return result

            except httpx.TimeoutException:
                msg = f"Timeout on attempt {attempt}/{self._max_retries}"
                logger.warning("OpenAlex: %s for query %r", msg, query.term)
                if attempt == self._max_retries:
                    result.errors.append(msg)

            except httpx.HTTPStatusError as e:
                msg = f"HTTP {e.response.status_code} for query {query.term!r}"
                logger.error("OpenAlex: %s", msg)
                result.errors.append(msg)
                return result

            except Exception as e:
                msg = f"Unexpected error: {e}"
                logger.error("OpenAlex: %s", msg)
                result.errors.append(msg)
                return result

            await asyncio.sleep(self._delay)

        return result

    def _parse_response(
        self,
        data: dict,
        query: SearchQuery,
    ) -> list[ResearchPaper]:
        """Parse OpenAlex works response into ResearchPaper objects."""
        papers = []
        for work in data.get("results", []):
            paper = self._parse_work(work, query)
            if paper is not None:
                papers.append(paper)
        return papers

    def _parse_work(
        self,
        work: dict,
        query: SearchQuery,
    ) -> ResearchPaper | None:
        """Parse a single OpenAlex work into a ResearchPaper."""
        try:
            openalex_id = work.get("id", "")
            if not openalex_id:
                return None

            # Strip the URL prefix — keep just the ID e.g. "W2741809807"
            source_id = openalex_id.split("/")[-1]

            title = (work.get("title") or "").strip()
            if not title:
                return None

            # DOI — strip URL prefix if present
            doi_raw = work.get("doi")
            doi = None
            if doi_raw:
                doi = doi_raw.replace("https://doi.org/", "").strip()

            # Abstract — OpenAlex stores it as an inverted index
            # e.g. {"We": [0], "study": [1, 5], ...} → reconstruct as text
            abstract = self._reconstruct_abstract(
                work.get("abstract_inverted_index")
            )

            # Published date
            published_date = None
            date_str = work.get("publication_date")
            if date_str:
                try:
                    published_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                except ValueError:
                    pass

            # Authors
            authors = []
            for authorship in work.get("authorships", []):
                author_data = authorship.get("author", {})
                name = author_data.get("display_name", "").strip()
                if name:
                    # Institution
                    affiliation = None
                    institutions = authorship.get("institutions", [])
                    if institutions:
                        affiliation = institutions[0].get("display_name")
                    authors.append(Author(name=name, affiliation=affiliation))

            # Journal
            journal = None
            primary_location = work.get("primary_location") or {}
            source = primary_location.get("source") or {}
            if source:
                journal = source.get("display_name")

            # Keywords
            keywords = [
                kw.get("display_name", "")
                for kw in work.get("keywords", [])
                if kw.get("display_name")
            ]

            source_ref = SourceReference(
                source_name=self.source_name,
                source_id=source_id,
                source_url=openalex_id,
                raw_metadata={
                    "openalex_id": openalex_id,
                    "cited_by_count": work.get("cited_by_count", 0),
                    "is_open_access": (work.get("open_access") or {}).get(
                        "is_oa", False
                    ),
                    "topic": query.topic_name,
                    "search_term": query.term,
                },
            )

            return ResearchPaper(
                doi=doi,
                title=title,
                abstract=abstract,
                published_date=published_date,
                authors=authors,
                journal=journal,
                keywords=keywords,
                sources=[source_ref],
                state=PaperState.DISCOVERED,
            )

        except Exception as e:
            logger.warning("OpenAlex: failed to parse work: %s", e)
            return None

    def _reconstruct_abstract(
        self,
        inverted_index: dict[str, list[int]] | None,
    ) -> str | None:
        """
        Reconstruct abstract text from OpenAlex inverted index format.
        OpenAlex stores abstracts as {word: [position, ...]} for copyright reasons.
        We reverse this into ordered text.
        """
        if not inverted_index:
            return None
        try:
            positions: dict[int, str] = {}
            for word, indices in inverted_index.items():
                for idx in indices:
                    positions[idx] = word
            return " ".join(positions[i] for i in sorted(positions))
        except Exception:
            return None