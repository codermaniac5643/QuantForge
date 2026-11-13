# src/quantforge/research/acquisition/resolver.py
"""
PDF URL resolver for QuantForge paper acquisition.

Resolution order (highest confidence first):
  1. arXiv ID  → https://arxiv.org/pdf/{id}
  2. is_oa flag in raw_metadata + oa_url present  → oa_url
  3. DOI       → Unpaywall API (best_oa_location.url_for_pdf)
  4. DOI       → Semantic Scholar API (openAccessPdf.url)
  5. No source → returns ResolvedUrl with url=None, reason recorded

All HTTP calls are async and use httpx. External API calls are
isolated behind a single async resolve() method so the manager
only needs one await.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from quantforge.models.paper import ResearchPaper

logger = logging.getLogger(__name__)

ARXIV_PDF_TEMPLATE = "https://arxiv.org/pdf/{arxiv_id}"
UNPAYWALL_URL = "https://api.unpaywall.org/v2/{doi}"
SEMANTIC_SCHOLAR_URL = (
    "https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}"
    "?fields=openAccessPdf"
)
_UA = "QuantForge/0.1 (research pipeline)"


@dataclass
class ResolvedUrl:
    """
    Result of URL resolution for a paper.

    Attributes:
        url:            The resolved PDF URL, or None if unavailable.
        source:         Which resolver found the URL
                        ("arxiv" | "oa_metadata" | "unpaywall" |
                         "semantic_scholar" | None).
        failure_reason: Explanation when url is None.
    """
    url: str | None
    source: str | None = None
    failure_reason: str | None = None


class PdfResolver:
    """
    Resolves a PDF download URL for a research paper.

    Uses an injected httpx.AsyncClient so callers control session
    lifecycle and tests can inject a mock client.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        mailto: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._client = client
        self._mailto = mailto
        self._timeout = timeout

    async def resolve(self, paper: ResearchPaper) -> ResolvedUrl:
        """
        Resolve the best available PDF URL for *paper*.
        Follows the mandated priority order:
          1 → arXiv  2 → OA metadata  3 → Unpaywall  4 → Semantic Scholar
        """
        # 1. arXiv
        if paper.arxiv_id:
            url = ARXIV_PDF_TEMPLATE.format(arxiv_id=paper.arxiv_id)
            logger.debug("Resolved via arXiv ID: %s", url)
            return ResolvedUrl(url=url, source="arxiv")

        # 2. OA URL in raw_metadata (from OpenAlex open_access field)
        if paper.doi:
            oa_url = self._extract_oa_url(paper)
            if oa_url:
                logger.debug("Resolved via OA metadata: %s", oa_url)
                return ResolvedUrl(url=oa_url, source="oa_metadata")

            # 3. Unpaywall
            unpaywall_url = await self._query_unpaywall(paper.doi)
            if unpaywall_url:
                return ResolvedUrl(url=unpaywall_url, source="unpaywall")

            # 4. Semantic Scholar
            ss_url = await self._query_semantic_scholar(paper.doi)
            if ss_url:
                return ResolvedUrl(url=ss_url, source="semantic_scholar")

            reason = (
                f"No open-access PDF found via Unpaywall or Semantic Scholar "
                f"for DOI {paper.doi!r}"
            )
        else:
            reason = "Paper has no arXiv ID or DOI — cannot resolve PDF URL"

        logger.info("Could not resolve PDF URL for paper %s: %s", paper.id, reason)
        return ResolvedUrl(url=None, source=None, failure_reason=reason)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _extract_oa_url(self, paper: ResearchPaper) -> str | None:
        """
        Check raw_metadata on each source reference for an OA PDF URL.
        OpenAlex sources carry is_open_access and sometimes oa_url.
        """
        for source_ref in paper.sources:
            meta = source_ref.raw_metadata or {}
            is_oa = meta.get("is_open_access", False)
            oa_url = meta.get("oa_url")
            if is_oa and oa_url:
                return oa_url
        return None

    async def _query_unpaywall(self, doi: str) -> str | None:
        """
        Query the Unpaywall API for the best OA PDF URL.
        Returns None on any error or when no PDF is available.
        """
        url = UNPAYWALL_URL.format(doi=doi)
        params: dict[str, str] = {}
        if self._mailto:
            params["email"] = self._mailto
        try:
            resp = await self._client.get(
                url,
                params=params,
                timeout=self._timeout,
                headers={"User-Agent": _UA},
                follow_redirects=True,
            )
            if resp.status_code == 404:
                logger.debug("Unpaywall: DOI %r not found", doi)
                return None
            resp.raise_for_status()
            data = resp.json()
            best = data.get("best_oa_location") or {}
            pdf_url = best.get("url_for_pdf")
            if pdf_url:
                logger.debug("Unpaywall resolved: %s", pdf_url)
            return pdf_url
        except Exception as exc:
            logger.warning("Unpaywall query failed for DOI %r: %s", doi, exc)
            return None

    async def _query_semantic_scholar(self, doi: str) -> str | None:
        """
        Query the Semantic Scholar API for an open-access PDF URL.
        Returns None on any error or when no PDF is available.
        """
        url = SEMANTIC_SCHOLAR_URL.format(doi=doi)
        try:
            resp = await self._client.get(
                url,
                timeout=self._timeout,
                headers={"User-Agent": _UA},
                follow_redirects=True,
            )
            if resp.status_code == 404:
                logger.debug("Semantic Scholar: DOI %r not found", doi)
                return None
            resp.raise_for_status()
            data = resp.json()
            oa = data.get("openAccessPdf") or {}
            pdf_url = oa.get("url")
            if pdf_url:
                logger.debug("Semantic Scholar resolved: %s", pdf_url)
            return pdf_url
        except Exception as exc:
            logger.warning(
                "Semantic Scholar query failed for DOI %r: %s", doi, exc
            )
            return None

