# tests/research/normalization/test_merger.py
"""
Unit tests for PaperMerger.

Covers:
  - Source references merged, no duplicates
  - Missing identifiers filled from incoming
  - Missing bibliographic fields filled from incoming
  - Existing fields not overwritten
  - Keywords merged, no case-insensitive duplicates
  - Authors merged, no duplicates; affiliation filled in if missing
  - Canonical UUID is preserved
  - State not changed by merge
"""

import pytest
from datetime import date
from uuid import uuid4

from quantforge.models.paper import Author, ResearchPaper, SourceReference
from quantforge.models.states import PaperState
from quantforge.research.normalization.merger import PaperMerger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_source(name: str, source_id: str) -> SourceReference:
    return SourceReference(source_name=name, source_id=source_id)


def make_paper(**kwargs) -> ResearchPaper:
    defaults = {
        "title": "A Research Paper",
        "state": PaperState.DISCOVERED,
    }
    defaults.update(kwargs)
    return ResearchPaper(**defaults)


# ---------------------------------------------------------------------------
# Source merging
# ---------------------------------------------------------------------------

class TestSourceMerging:
    def test_new_source_appended(self):
        arxiv_src = make_source("arxiv", "2401.00001")
        canonical = make_paper(sources=[arxiv_src])

        openalex_src = make_source("openalex", "W12345")
        incoming = make_paper(sources=[openalex_src])

        merger = PaperMerger()
        merger.merge(canonical, incoming)

        assert len(canonical.sources) == 2
        source_names = {s.source_name for s in canonical.sources}
        assert "arxiv" in source_names
        assert "openalex" in source_names

    def test_duplicate_source_not_added(self):
        """Same source_name + source_id pair should not be added twice."""
        arxiv_src = make_source("arxiv", "2401.00001")
        canonical = make_paper(sources=[arxiv_src])

        arxiv_src_dup = make_source("arxiv", "2401.00001")
        incoming = make_paper(sources=[arxiv_src_dup])

        merger = PaperMerger()
        merger.merge(canonical, incoming)

        assert len(canonical.sources) == 1

    def test_multiple_new_sources_all_appended(self):
        arxiv_src = make_source("arxiv", "2401.00001")
        canonical = make_paper(sources=[arxiv_src])

        openalex_src = make_source("openalex", "W111")
        crossref_src = make_source("crossref", "10.1234/x")
        incoming = make_paper(sources=[openalex_src, crossref_src])

        merger = PaperMerger()
        merger.merge(canonical, incoming)

        assert len(canonical.sources) == 3


# ---------------------------------------------------------------------------
# Identifier filling
# ---------------------------------------------------------------------------

class TestIdentifierFilling:
    def test_missing_doi_filled_from_incoming(self):
        canonical = make_paper(doi=None)
        incoming = make_paper(doi="10.1234/example")

        PaperMerger().merge(canonical, incoming)

        assert canonical.doi == "10.1234/example"

    def test_existing_doi_not_overwritten(self):
        canonical = make_paper(doi="10.1234/original")
        incoming = make_paper(doi="10.9999/new")

        PaperMerger().merge(canonical, incoming)

        assert canonical.doi == "10.1234/original"

    def test_missing_arxiv_id_filled(self):
        canonical = make_paper(arxiv_id=None)
        incoming = make_paper(arxiv_id="2401.00001")

        PaperMerger().merge(canonical, incoming)

        assert canonical.arxiv_id == "2401.00001"

    def test_existing_arxiv_id_not_overwritten(self):
        canonical = make_paper(arxiv_id="2401.00001")
        incoming = make_paper(arxiv_id="9999.99999")

        PaperMerger().merge(canonical, incoming)

        assert canonical.arxiv_id == "2401.00001"


# ---------------------------------------------------------------------------
# Bibliographic field filling
# ---------------------------------------------------------------------------

class TestBibliographicFilling:
    def test_missing_abstract_filled(self):
        canonical = make_paper(abstract=None)
        incoming = make_paper(abstract="This paper studies momentum.")

        PaperMerger().merge(canonical, incoming)

        assert canonical.abstract == "This paper studies momentum."

    def test_existing_abstract_not_overwritten(self):
        canonical = make_paper(abstract="Original abstract.")
        incoming = make_paper(abstract="New abstract.")

        PaperMerger().merge(canonical, incoming)

        assert canonical.abstract == "Original abstract."

    def test_missing_published_date_filled(self):
        canonical = make_paper(published_date=None)
        incoming = make_paper(published_date=date(2024, 1, 15))

        PaperMerger().merge(canonical, incoming)

        assert canonical.published_date == date(2024, 1, 15)

    def test_existing_published_date_not_overwritten(self):
        canonical = make_paper(published_date=date(2024, 1, 10))
        incoming = make_paper(published_date=date(2024, 6, 1))

        PaperMerger().merge(canonical, incoming)

        assert canonical.published_date == date(2024, 1, 10)

    def test_missing_journal_filled(self):
        canonical = make_paper(journal=None)
        incoming = make_paper(journal="Journal of Finance")

        PaperMerger().merge(canonical, incoming)

        assert canonical.journal == "Journal of Finance"

    def test_existing_journal_not_overwritten(self):
        canonical = make_paper(journal="Original Journal")
        incoming = make_paper(journal="New Journal")

        PaperMerger().merge(canonical, incoming)

        assert canonical.journal == "Original Journal"


# ---------------------------------------------------------------------------
# Keyword merging
# ---------------------------------------------------------------------------

class TestKeywordMerging:
    def test_new_keywords_appended(self):
        canonical = make_paper(keywords=["momentum"])
        incoming = make_paper(keywords=["trend-following", "equities"])

        PaperMerger().merge(canonical, incoming)

        assert "momentum" in canonical.keywords
        assert "trend-following" in canonical.keywords
        assert "equities" in canonical.keywords

    def test_duplicate_keywords_not_added(self):
        canonical = make_paper(keywords=["Momentum"])
        incoming = make_paper(keywords=["momentum"])  # same, different case

        PaperMerger().merge(canonical, incoming)

        assert len([k for k in canonical.keywords if k.lower() == "momentum"]) == 1

    def test_no_keywords_in_incoming_no_change(self):
        canonical = make_paper(keywords=["momentum"])
        incoming = make_paper(keywords=[])

        PaperMerger().merge(canonical, incoming)

        assert canonical.keywords == ["momentum"]


# ---------------------------------------------------------------------------
# Author merging
# ---------------------------------------------------------------------------

class TestAuthorMerging:
    def test_new_authors_appended(self):
        canonical = make_paper(authors=[Author(name="Alice")])
        incoming = make_paper(authors=[Author(name="Bob")])

        PaperMerger().merge(canonical, incoming)

        names = {a.name for a in canonical.authors}
        assert "Alice" in names
        assert "Bob" in names

    def test_duplicate_authors_not_added(self):
        canonical = make_paper(authors=[Author(name="Alice Smith")])
        incoming = make_paper(authors=[Author(name="Alice Smith")])

        PaperMerger().merge(canonical, incoming)

        assert len(canonical.authors) == 1

    def test_author_case_insensitive_dedup(self):
        canonical = make_paper(authors=[Author(name="Alice Smith")])
        incoming = make_paper(authors=[Author(name="alice smith")])

        PaperMerger().merge(canonical, incoming)

        assert len(canonical.authors) == 1

    def test_missing_affiliation_filled(self):
        canonical = make_paper(authors=[Author(name="Alice", affiliation=None)])
        incoming = make_paper(authors=[Author(name="Alice", affiliation="MIT")])

        PaperMerger().merge(canonical, incoming)

        alice = next(a for a in canonical.authors if a.name == "Alice")
        assert alice.affiliation == "MIT"

    def test_existing_affiliation_not_overwritten(self):
        canonical = make_paper(authors=[Author(name="Alice", affiliation="Harvard")])
        incoming = make_paper(authors=[Author(name="Alice", affiliation="MIT")])

        PaperMerger().merge(canonical, incoming)

        alice = next(a for a in canonical.authors if a.name == "Alice")
        assert alice.affiliation == "Harvard"


# ---------------------------------------------------------------------------
# Identity preservation
# ---------------------------------------------------------------------------

class TestIdentityPreservation:
    def test_canonical_uuid_preserved(self):
        original_id = uuid4()
        canonical = make_paper()
        canonical.id = original_id  # type: ignore[assignment]

        incoming = make_paper()
        PaperMerger().merge(canonical, incoming)

        assert canonical.id == original_id

    def test_state_not_changed_by_merge(self):
        canonical = make_paper(state=PaperState.NORMALIZED)
        incoming = make_paper(state=PaperState.DISCOVERED)

        PaperMerger().merge(canonical, incoming)

        assert canonical.state == PaperState.NORMALIZED

    def test_returns_same_canonical_instance(self):
        canonical = make_paper()
        incoming = make_paper()

        result = PaperMerger().merge(canonical, incoming)

        assert result is canonical


# ---------------------------------------------------------------------------
# Full merge scenario
# ---------------------------------------------------------------------------

class TestFullMerge:
    def test_full_merge_combines_all_fields(self):
        """
        Simulate merging an OpenAlex record into an arXiv canonical record.
        The arXiv record has the arXiv ID and abstract; OpenAlex adds DOI,
        journal, and a second source reference.
        """
        arxiv_src = make_source("arxiv", "2401.00001")
        canonical = make_paper(
            title="Momentum Investing in Equity Markets",
            arxiv_id="2401.00001",
            abstract="We study cross-sectional momentum.",
            doi=None,
            journal=None,
            keywords=["q-fin.PM"],
            authors=[Author(name="Jane Smith")],
            sources=[arxiv_src],
        )

        openalex_src = make_source("openalex", "W12345678")
        incoming = make_paper(
            title="Momentum Investing in Equity Markets",
            arxiv_id=None,
            doi="10.1234/momentum",
            abstract=None,            # already in canonical
            journal="Journal of Finance",
            keywords=["momentum", "equities"],
            authors=[
                Author(name="Jane Smith", affiliation="MIT"),
                Author(name="John Doe"),
            ],
            sources=[openalex_src],
        )

        PaperMerger().merge(canonical, incoming)

        # Identifiers
        assert canonical.doi == "10.1234/momentum"
        assert canonical.arxiv_id == "2401.00001"

        # Bibliographic
        assert canonical.abstract == "We study cross-sectional momentum."  # preserved
        assert canonical.journal == "Journal of Finance"

        # Sources
        assert len(canonical.sources) == 2

        # Keywords
        assert "q-fin.PM" in canonical.keywords
        assert "momentum" in canonical.keywords
        assert "equities" in canonical.keywords

        # Authors
        names = {a.name for a in canonical.authors}
        assert "Jane Smith" in names
        assert "John Doe" in names

        # Jane's affiliation filled from OpenAlex
        jane = next(a for a in canonical.authors if a.name == "Jane Smith")
        assert jane.affiliation == "MIT"

