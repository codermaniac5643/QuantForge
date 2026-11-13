# src/quantforge/research/discovery/arxiv.py

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx
from xml.etree import ElementTree as ET

from quantforge.models.paper import ResearchPaper, SourceReference, Author
from quantforge.models.states import PaperState
from quantforge.research.discovery.base import (
    BaseConnector,
    SearchQuery,
    DiscoveryResult,
)

logger = logging.getLogger(__name__)

ARXIV_API_URL = "https://export.arxiv.org/api/query"
ARXIV_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
}

# arXiv does NOT support wildcard category filters (e.g. "cat:q-fin.*" silently
# fails and returns results from all categories).  We enumerate only the
# sub-categories that are relevant to QuantForge's trading research scope.
_FINANCE_CATEGORIES = (
    "cat:q-fin.PM",   # Portfolio Management
    "cat:q-fin.ST",   # Statistical Finance
    "cat:q-fin.TR",   # Trading and Market Microstructure
    "cat:q-fin.GN",   # General Finance
    "cat:q-fin.RM",   # Risk Management
)
_FINANCE_CATEGORY_FILTER = "(" + " OR ".join(_FINANCE_CATEGORIES) + ")"


class ArxivConnector(BaseConnector):
    """
    Discovers research papers from arXiv using their public API.
    Uses the Atom XML feed — no authentication required.
    Rate limit: 1 request per 3 seconds (arXiv policy).
    """

    def __init__(
        self,
        request_delay: float = 3.0,
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        self._delay = request_delay
        self._timeout = timeout
        self._max_retries = max_retries
        self._client: httpx.AsyncClient | None = None

    @property
    def source_name(self) -> str:
        return "arxiv"

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

        # Build the date filter
        cutoff = datetime.now(timezone.utc) - timedelta(days=query.lookback_days)
        date_filter = cutoff.strftime("%Y%m%d")

        # Scope the term to title and abstract only — using `all:` also matches
        # category tags, comments, and journal refs, letting unrelated papers
        # (e.g. physics papers containing "momentum") slip through even when a
        # category filter is applied.
        # We also enumerate finance categories explicitly: "cat:q-fin.*" is a
        # wildcard that the arXiv API silently ignores, returning results from
        # all categories.
        term_parts = query.term.split()
        if len(term_parts) == 1:
            term_filter = f"(ti:{term_parts[0]} OR abs:{term_parts[0]})"
        else:
            # Match each individual word in title OR abstract
            ti_parts = " OR ".join(f"ti:{w}" for w in term_parts)
            abs_parts = " OR ".join(f"abs:{w}" for w in term_parts)
            term_filter = f"({ti_parts} OR {abs_parts})"

        search_query = (
            f"{term_filter} AND "
            f"{_FINANCE_CATEGORY_FILTER} AND "
            f"submittedDate:[{date_filter}0000 TO 99991231235959]"
        )

        params = {
            "search_query": search_query,
            "max_results": query.max_results,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }

        for attempt in range(1, self._max_retries + 1):
            try:
                await asyncio.sleep(self._delay if attempt > 1 else 0)
                client = self._get_client()
                response = await client.get(ARXIV_API_URL, params=params)
                response.raise_for_status()

                papers, total = self._parse_response(response.text, query)
                result.papers = papers
                result.total_found = total
                result.total_fetched = len(papers)

                logger.info(
                    "arXiv search %r — fetched %d / %d results",
                    query.term,
                    result.total_fetched,
                    result.total_found,
                )
                return result

            except httpx.TimeoutException:
                msg = f"Timeout on attempt {attempt}/{self._max_retries}"
                logger.warning("arXiv: %s for query %r", msg, query.term)
                if attempt == self._max_retries:
                    result.errors.append(msg)

            except httpx.HTTPStatusError as e:
                msg = f"HTTP {e.response.status_code} for query {query.term!r}"
                logger.error("arXiv: %s", msg)
                result.errors.append(msg)
                return result

            except Exception as e:
                msg = f"Unexpected error: {e}"
                logger.error("arXiv: %s", msg)
                result.errors.append(msg)
                return result

            await asyncio.sleep(self._delay)

        return result

    def _parse_response(
        self,
        xml_text: str,
        query: SearchQuery,
    ) -> tuple[list[ResearchPaper], int]:
        """Parse arXiv Atom XML into ResearchPaper objects."""
        root = ET.fromstring(xml_text)

        # Total results reported by arXiv
        total_el = root.find("opensearch:totalResults", ARXIV_NS)
        total = int(total_el.text) if total_el is not None else 0

        papers: list[ResearchPaper] = []

        for entry in root.findall("atom:entry", ARXIV_NS):
            paper = self._parse_entry(entry, query)
            if paper is not None:
                papers.append(paper)

        return papers, total

    def _parse_entry(
        self,
        entry: ET.Element,
        query: SearchQuery,
    ) -> ResearchPaper | None:
        """Parse a single Atom entry into a ResearchPaper."""
        try:
            # arXiv ID lives in the <id> tag as a URL
            id_el = entry.find("atom:id", ARXIV_NS)
            if id_el is None or not id_el.text:
                return None

            arxiv_url = id_el.text.strip()
            # Extract just the ID portion e.g. "2401.00001v1" → "2401.00001"
            arxiv_id = arxiv_url.split("/abs/")[-1].split("v")[0]

            title_el = entry.find("atom:title", ARXIV_NS)
            title = (title_el.text or "").strip().replace("\n", " ") if title_el is not None else ""
            if not title:
                return None

            abstract_el = entry.find("atom:summary", ARXIV_NS)
            abstract = (abstract_el.text or "").strip().replace("\n", " ") if abstract_el is not None else None

            # Published date
            published_el = entry.find("atom:published", ARXIV_NS)
            published_date = None
            if published_el is not None and published_el.text:
                try:
                    published_date = datetime.fromisoformat(
                        published_el.text.replace("Z", "+00:00")
                    ).date()
                except ValueError:
                    pass

            # Authors
            authors = []
            for author_el in entry.findall("atom:author", ARXIV_NS):
                name_el = author_el.find("atom:name", ARXIV_NS)
                if name_el is not None and name_el.text:
                    authors.append(Author(name=name_el.text.strip()))

            # DOI — arXiv sometimes provides one
            doi = None
            doi_el = entry.find("arxiv:doi", ARXIV_NS)
            if doi_el is not None and doi_el.text:
                doi = doi_el.text.strip()

            # Categories as keywords
            keywords = []
            for cat_el in entry.findall("atom:category", ARXIV_NS):
                term = cat_el.get("term")
                if term:
                    keywords.append(term)

            source = SourceReference(
                source_name=self.source_name,
                source_id=arxiv_id,
                source_url=arxiv_url,
                raw_metadata={
                    "arxiv_url": arxiv_url,
                    "topic": query.topic_name,
                    "search_term": query.term,
                },
            )

            return ResearchPaper(
                arxiv_id=arxiv_id,
                doi=doi,
                title=title,
                abstract=abstract,
                published_date=published_date,
                authors=authors,
                keywords=keywords,
                sources=[source],
                state=PaperState.DISCOVERED,
            )

        except Exception as e:
            logger.warning("arXiv: failed to parse entry: %s", e)
            return None