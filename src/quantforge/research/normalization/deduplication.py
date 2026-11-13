# src/quantforge/research/normalization/deduplication.py
"""
Deduplication service for QuantForge research papers.

Dedup hierarchy (highest to lowest confidence):
  1. DOI          — exact normalized match
  2. arXiv ID     — exact normalized match
  3. Title        — normalized title exact match (fallback only)

A DeduplicationResult records *why* two records match so that every
dedup decision is traceable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from quantforge.models.paper import ResearchPaper
from quantforge.research.normalization.text import (
    normalize_doi,
    normalize_arxiv_id,
    normalize_title,
)

logger = logging.getLogger(__name__)


@dataclass
class DeduplicationResult:
    """
    Result of checking whether an incoming paper already exists in a collection.

    Attributes:
        is_duplicate:   True if the paper already exists.
        matched_id:     Internal UUID of the existing canonical paper when
                        is_duplicate is True.
        match_method:   How the duplicate was identified
                        ("doi" | "arxiv_id" | "title" | None).
        evidence:       Human-readable explanation of the match.
    """

    is_duplicate: bool
    matched_id: UUID | None = None
    match_method: str | None = None
    evidence: str | None = None


class Deduplicator:
    """
    Checks whether a new paper duplicates one already in a collection.

    Usage:
        deduplicator = Deduplicator(existing_papers)
        result = deduplicator.check(new_paper)
        if result.is_duplicate:
            # merge sources into existing paper
            ...
        else:
            # add new_paper to the collection
            ...

    The deduplicator is built from a snapshot of existing papers.
    Call rebuild() or create a new instance when the collection changes.

    Thread safety: Not thread-safe. Each pipeline stage should use
    its own instance.
    """

    def __init__(self, existing_papers: list[ResearchPaper] | None = None) -> None:
        # Lookup indices: normalized key → paper UUID
        self._doi_index: dict[str, UUID] = {}
        self._arxiv_index: dict[str, UUID] = {}
        self._title_index: dict[str, UUID] = {}

        for paper in (existing_papers or []):
            self._index_paper(paper)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def check(self, paper: ResearchPaper) -> DeduplicationResult:
        """
        Check whether *paper* duplicates an existing record.

        Checks are performed in DOI → arXiv ID → title order and stop
        at the first match.
        """
        # 1. DOI match
        norm_doi = normalize_doi(paper.doi)
        if norm_doi and norm_doi in self._doi_index:
            matched_id = self._doi_index[norm_doi]
            logger.debug(
                "Duplicate detected via DOI %r → %s", norm_doi, matched_id
            )
            return DeduplicationResult(
                is_duplicate=True,
                matched_id=matched_id,
                match_method="doi",
                evidence=f"DOI match: normalized doi={norm_doi!r}",
            )

        # 2. arXiv ID match
        norm_arxiv = normalize_arxiv_id(paper.arxiv_id)
        if norm_arxiv and norm_arxiv in self._arxiv_index:
            matched_id = self._arxiv_index[norm_arxiv]
            logger.debug(
                "Duplicate detected via arXiv ID %r → %s", norm_arxiv, matched_id
            )
            return DeduplicationResult(
                is_duplicate=True,
                matched_id=matched_id,
                match_method="arxiv_id",
                evidence=f"arXiv ID match: normalized id={norm_arxiv!r}",
            )

        # 3. Normalized title fallback
        norm_title = normalize_title(paper.title)
        if norm_title and norm_title in self._title_index:
            matched_id = self._title_index[norm_title]
            logger.debug(
                "Duplicate detected via title %r → %s", norm_title, matched_id
            )
            return DeduplicationResult(
                is_duplicate=True,
                matched_id=matched_id,
                match_method="title",
                evidence=f"Title match: normalized title={norm_title!r}",
            )

        return DeduplicationResult(is_duplicate=False)

    def register(self, paper: ResearchPaper) -> None:
        """
        Add *paper* to the deduplicator's index so future papers are checked
        against it.

        Call this after deciding a paper is new and adding it to the
        collection.
        """
        self._index_paper(paper)

    def rebuild(self, papers: list[ResearchPaper]) -> None:
        """
        Rebuild the index from scratch from a new list of papers.
        Replaces any previously indexed papers.
        """
        self._doi_index.clear()
        self._arxiv_index.clear()
        self._title_index.clear()
        for paper in papers:
            self._index_paper(paper)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _index_paper(self, paper: ResearchPaper) -> None:
        """Add a paper to the lookup indices."""
        norm_doi = normalize_doi(paper.doi)
        if norm_doi:
            if norm_doi in self._doi_index:
                logger.warning(
                    "DOI collision while building index: %r already maps to %s, "
                    "skipping %s",
                    norm_doi,
                    self._doi_index[norm_doi],
                    paper.id,
                )
            else:
                self._doi_index[norm_doi] = paper.id

        norm_arxiv = normalize_arxiv_id(paper.arxiv_id)
        if norm_arxiv:
            if norm_arxiv in self._arxiv_index:
                logger.warning(
                    "arXiv ID collision while building index: %r already maps to %s, "
                    "skipping %s",
                    norm_arxiv,
                    self._arxiv_index[norm_arxiv],
                    paper.id,
                )
            else:
                self._arxiv_index[norm_arxiv] = paper.id

        norm_title = normalize_title(paper.title)
        if norm_title:
            if norm_title in self._title_index:
                logger.debug(
                    "Title collision while building index: %r already maps to %s, "
                    "skipping %s",
                    norm_title,
                    self._title_index[norm_title],
                    paper.id,
                )
            else:
                self._title_index[norm_title] = paper.id

