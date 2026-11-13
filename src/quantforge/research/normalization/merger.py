# src/quantforge/research/normalization/merger.py
"""
PaperMerger — conflict-resolution and source-merging for duplicate papers.

When a newly discovered paper duplicates an existing canonical record,
the merger:
  1. Appends any new source references.
  2. Fills in missing metadata fields (DOI, arXiv ID, abstract, etc.)
     from the new record — never overwrites existing values.
  3. Extends keywords without creating duplicates.
  4. Extends authors without creating duplicates.
  5. Keeps the original paper's UUID and state.
  6. Never fabricates data or overwrites populated fields.

The canonical paper is modified in-place and returned so callers can
persist the updated record.
"""

from __future__ import annotations

import logging

from quantforge.models.paper import Author, ResearchPaper, SourceReference

logger = logging.getLogger(__name__)


class PaperMerger:
    """
    Merges an incoming paper's metadata and source references into an
    existing canonical paper.

    Design principles:
    - Existing populated fields are never overwritten.
    - Missing fields are filled from the incoming record.
    - Source references from the new paper are appended if not already
      present (checked by source_id).
    - Keywords are merged without duplicates (case-insensitive).
    - Authors are merged without duplicates (by exact name).
    - The returned object is the same canonical instance (modified in-place).
    """

    def merge(
        self,
        canonical: ResearchPaper,
        incoming: ResearchPaper,
    ) -> ResearchPaper:
        """
        Merge *incoming* into *canonical*.

        Returns *canonical* (the same instance, modified in-place).
        """
        logger.debug(
            "Merging incoming paper %s into canonical %s",
            incoming.id,
            canonical.id,
        )

        self._merge_identifiers(canonical, incoming)
        self._merge_bibliographic(canonical, incoming)
        self._merge_sources(canonical, incoming)
        self._merge_keywords(canonical, incoming)
        self._merge_authors(canonical, incoming)

        return canonical

    # ------------------------------------------------------------------
    # Private merge helpers
    # ------------------------------------------------------------------

    def _merge_identifiers(
        self, canonical: ResearchPaper, incoming: ResearchPaper
    ) -> None:
        """Fill in missing DOI or arXiv ID from the incoming record."""
        if canonical.doi is None and incoming.doi:
            logger.debug(
                "Paper %s: filling missing DOI from incoming: %s",
                canonical.id,
                incoming.doi,
            )
            canonical.doi = incoming.doi

        if canonical.arxiv_id is None and incoming.arxiv_id:
            logger.debug(
                "Paper %s: filling missing arXiv ID from incoming: %s",
                canonical.id,
                incoming.arxiv_id,
            )
            canonical.arxiv_id = incoming.arxiv_id

    def _merge_bibliographic(
        self, canonical: ResearchPaper, incoming: ResearchPaper
    ) -> None:
        """Fill in missing bibliographic fields from the incoming record."""
        if canonical.abstract is None and incoming.abstract:
            canonical.abstract = incoming.abstract

        if canonical.published_date is None and incoming.published_date:
            canonical.published_date = incoming.published_date

        if canonical.journal is None and incoming.journal:
            canonical.journal = incoming.journal

    def _merge_sources(
        self, canonical: ResearchPaper, incoming: ResearchPaper
    ) -> None:
        """
        Append source references from *incoming* that are not already
        present in *canonical*.

        Presence is checked by (source_name, source_id) pair.
        """
        existing_keys: set[tuple[str, str]] = {
            (s.source_name, s.source_id) for s in canonical.sources
        }
        for source in incoming.sources:
            key = (source.source_name, source.source_id)
            if key not in existing_keys:
                canonical.sources.append(source)
                existing_keys.add(key)
                logger.debug(
                    "Paper %s: added source %s/%s",
                    canonical.id,
                    source.source_name,
                    source.source_id,
                )

    def _merge_keywords(
        self, canonical: ResearchPaper, incoming: ResearchPaper
    ) -> None:
        """
        Merge keywords from *incoming* into *canonical*.
        Comparison is case-insensitive; original casing from canonical is kept.
        """
        existing_lower: set[str] = {kw.lower() for kw in canonical.keywords}
        for kw in incoming.keywords:
            if kw.lower() not in existing_lower:
                canonical.keywords.append(kw)
                existing_lower.add(kw.lower())

    def _merge_authors(
        self, canonical: ResearchPaper, incoming: ResearchPaper
    ) -> None:
        """
        Merge authors from *incoming* into *canonical*.

        Authors are compared by exact name match (case-insensitive).
        When a matching author already exists, their affiliation is filled in
        if the existing author has none.
        """
        existing_names: dict[str, Author] = {
            a.name.lower(): a for a in canonical.authors
        }
        for author in incoming.authors:
            key = author.name.lower()
            if key in existing_names:
                # Fill in affiliation if the existing author lacks one
                existing = existing_names[key]
                if existing.affiliation is None and author.affiliation:
                    existing.affiliation = author.affiliation
            else:
                canonical.authors.append(author)
                existing_names[key] = author

