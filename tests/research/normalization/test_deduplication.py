# tests/research/normalization/test_deduplication.py
"""
Unit tests for the Deduplicator.

Covers:
  - DOI-based deduplication (highest priority)
  - arXiv ID-based deduplication
  - Normalized-title fallback deduplication
  - No duplicate: genuinely new paper
  - Match evidence is recorded
  - register() makes a new paper detectable in future checks
  - rebuild() replaces the index
  - Collisions in the index are handled gracefully
"""

import pytest
from uuid import uuid4

from quantforge.models.paper import ResearchPaper, SourceReference, Author
from quantforge.models.states import PaperState
from quantforge.research.normalization.deduplication import Deduplicator, DeduplicationResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_paper(
    title: str = "A Test Paper",
    doi: str | None = None,
    arxiv_id: str | None = None,
) -> ResearchPaper:
    return ResearchPaper(
        title=title,
        doi=doi,
        arxiv_id=arxiv_id,
        state=PaperState.DISCOVERED,
    )


def make_source(name: str = "arxiv", source_id: str = "2401.00001") -> SourceReference:
    return SourceReference(source_name=name, source_id=source_id)


# ---------------------------------------------------------------------------
# DOI-based deduplication
# ---------------------------------------------------------------------------

class TestDoiDeduplication:
    def test_exact_doi_match(self):
        existing = make_paper(doi="10.1234/example")
        dedup = Deduplicator([existing])

        incoming = make_paper(title="Different Title But Same DOI", doi="10.1234/example")
        result = dedup.check(incoming)

        assert result.is_duplicate
        assert result.matched_id == existing.id
        assert result.match_method == "doi"

    def test_doi_with_url_prefix_matches_bare_doi(self):
        existing = make_paper(doi="10.1234/example")
        dedup = Deduplicator([existing])

        # Incoming has the same DOI but with URL prefix
        incoming = make_paper(doi="https://doi.org/10.1234/example")
        result = dedup.check(incoming)

        assert result.is_duplicate
        assert result.match_method == "doi"

    def test_doi_case_insensitive(self):
        existing = make_paper(doi="10.1234/EXAMPLE")
        dedup = Deduplicator([existing])

        incoming = make_paper(doi="10.1234/example")
        result = dedup.check(incoming)

        assert result.is_duplicate
        assert result.match_method == "doi"

    def test_different_dois_not_duplicate(self):
        existing = make_paper(title="DOI Paper Alpha", doi="10.1234/paper_a")
        dedup = Deduplicator([existing])

        incoming = make_paper(title="DOI Paper Beta", doi="10.5678/paper_b")
        result = dedup.check(incoming)

        assert not result.is_duplicate


# ---------------------------------------------------------------------------
# arXiv ID-based deduplication
# ---------------------------------------------------------------------------

class TestArxivIdDeduplication:
    def test_exact_arxiv_id_match(self):
        existing = make_paper(arxiv_id="2401.00001")
        dedup = Deduplicator([existing])

        incoming = make_paper(arxiv_id="2401.00001")
        result = dedup.check(incoming)

        assert result.is_duplicate
        assert result.matched_id == existing.id
        assert result.match_method == "arxiv_id"

    def test_arxiv_id_with_version_suffix_matches(self):
        existing = make_paper(arxiv_id="2401.00001")
        dedup = Deduplicator([existing])

        incoming = make_paper(arxiv_id="2401.00001v2")
        result = dedup.check(incoming)

        assert result.is_duplicate
        assert result.match_method == "arxiv_id"

    def test_arxiv_id_url_format_matches(self):
        existing = make_paper(arxiv_id="2401.00001")
        dedup = Deduplicator([existing])

        incoming = make_paper(arxiv_id="https://arxiv.org/abs/2401.00001v1")
        result = dedup.check(incoming)

        assert result.is_duplicate
        assert result.match_method == "arxiv_id"

    def test_different_arxiv_ids_not_duplicate(self):
        existing = make_paper(title="ArXiv Paper One", arxiv_id="2401.00001")
        dedup = Deduplicator([existing])

        incoming = make_paper(title="ArXiv Paper Two", arxiv_id="2401.00002")
        result = dedup.check(incoming)

        assert not result.is_duplicate

    def test_arxiv_takes_priority_over_title(self):
        """arXiv ID match should be reported, not title match."""
        existing = make_paper(title="A Momentum Study", arxiv_id="2401.00001")
        dedup = Deduplicator([existing])

        incoming = make_paper(title="A Momentum Study", arxiv_id="2401.00001")
        result = dedup.check(incoming)

        # DOI is None so arXiv check runs and matches first
        assert result.is_duplicate
        assert result.match_method == "arxiv_id"


# ---------------------------------------------------------------------------
# Title-based deduplication
# ---------------------------------------------------------------------------

class TestTitleDeduplication:
    def test_exact_title_match(self):
        existing = make_paper(title="Momentum Investing in Equities")
        dedup = Deduplicator([existing])

        incoming = make_paper(title="Momentum Investing in Equities")
        result = dedup.check(incoming)

        assert result.is_duplicate
        assert result.matched_id == existing.id
        assert result.match_method == "title"

    def test_title_match_case_insensitive(self):
        existing = make_paper(title="Momentum Investing in Equities")
        dedup = Deduplicator([existing])

        incoming = make_paper(title="momentum investing in equities")
        result = dedup.check(incoming)

        assert result.is_duplicate
        assert result.match_method == "title"

    def test_title_match_ignores_punctuation(self):
        existing = make_paper(title="Momentum Investing: A Study")
        dedup = Deduplicator([existing])

        incoming = make_paper(title="Momentum Investing  A Study")
        result = dedup.check(incoming)

        assert result.is_duplicate
        assert result.match_method == "title"

    def test_different_titles_not_duplicate(self):
        existing = make_paper(title="Momentum Investing in Equities")
        dedup = Deduplicator([existing])

        incoming = make_paper(title="Mean Reversion Strategies")
        result = dedup.check(incoming)

        assert not result.is_duplicate


# ---------------------------------------------------------------------------
# Match priority: DOI > arXiv > title
# ---------------------------------------------------------------------------

class TestMatchPriority:
    def test_doi_takes_priority_over_arxiv(self):
        """When both DOI and arXiv ID match the same existing paper, DOI is reported."""
        existing = make_paper(doi="10.1234/example", arxiv_id="2401.00001")
        dedup = Deduplicator([existing])

        incoming = make_paper(doi="10.1234/example", arxiv_id="2401.00001")
        result = dedup.check(incoming)

        assert result.is_duplicate
        assert result.match_method == "doi"


# ---------------------------------------------------------------------------
# No duplicate
# ---------------------------------------------------------------------------

class TestNoDuplicate:
    def test_new_paper_not_duplicate(self):
        existing = make_paper(doi="10.1234/existing", title="Existing Paper")
        dedup = Deduplicator([existing])

        incoming = make_paper(doi="10.9999/new_paper", title="New Paper Title")
        result = dedup.check(incoming)

        assert not result.is_duplicate
        assert result.matched_id is None
        assert result.match_method is None

    def test_empty_collection_never_duplicates(self):
        dedup = Deduplicator([])
        incoming = make_paper(doi="10.1234/anything")
        result = dedup.check(incoming)
        assert not result.is_duplicate

    def test_paper_with_no_identifiers_no_duplicate(self):
        existing = make_paper(title="A Paper With No Identifiers")
        dedup = Deduplicator([existing])

        incoming = make_paper(title="Completely Different Title")
        result = dedup.check(incoming)

        assert not result.is_duplicate


# ---------------------------------------------------------------------------
# Match evidence
# ---------------------------------------------------------------------------

class TestMatchEvidence:
    def test_doi_match_records_evidence(self):
        existing = make_paper(doi="10.1234/example")
        dedup = Deduplicator([existing])

        incoming = make_paper(doi="10.1234/example")
        result = dedup.check(incoming)

        assert result.evidence is not None
        assert "doi" in result.evidence.lower()

    def test_arxiv_match_records_evidence(self):
        existing = make_paper(arxiv_id="2401.00001")
        dedup = Deduplicator([existing])

        incoming = make_paper(arxiv_id="2401.00001")
        result = dedup.check(incoming)

        assert result.evidence is not None
        assert "arxiv" in result.evidence.lower()

    def test_title_match_records_evidence(self):
        existing = make_paper(title="Momentum Investing")
        dedup = Deduplicator([existing])

        incoming = make_paper(title="Momentum Investing")
        result = dedup.check(incoming)

        assert result.evidence is not None
        assert "title" in result.evidence.lower()


# ---------------------------------------------------------------------------
# register() and rebuild()
# ---------------------------------------------------------------------------

class TestRegisterAndRebuild:
    def test_register_makes_new_paper_detectable(self):
        dedup = Deduplicator([])

        new_paper = make_paper(doi="10.1234/new")
        # Not yet registered — should not be a duplicate
        result_before = dedup.check(new_paper)
        assert not result_before.is_duplicate

        # Register it
        dedup.register(new_paper)

        # Now a second paper with the same DOI should be a duplicate
        duplicate = make_paper(doi="10.1234/new")
        result_after = dedup.check(duplicate)
        assert result_after.is_duplicate

    def test_rebuild_replaces_index(self):
        old_paper = make_paper(title="Old DOI Paper", doi="10.1234/old")
        dedup = Deduplicator([old_paper])

        # Confirm old paper is indexed
        assert dedup.check(make_paper(title="Old DOI Paper", doi="10.1234/old")).is_duplicate

        # Rebuild with new set
        new_paper = make_paper(title="New DOI Paper", doi="10.9999/new")
        dedup.rebuild([new_paper])

        # Old paper is gone (use a unique title so title index doesn't fire)
        assert not dedup.check(make_paper(title="Entirely Different Title", doi="10.1234/old")).is_duplicate
        # New paper is present
        assert dedup.check(make_paper(title="New DOI Paper", doi="10.9999/new")).is_duplicate

    def test_rebuild_with_empty_list_clears_index(self):
        existing = make_paper(doi="10.1234/existing")
        dedup = Deduplicator([existing])

        dedup.rebuild([])
        result = dedup.check(make_paper(doi="10.1234/existing"))
        assert not result.is_duplicate


# ---------------------------------------------------------------------------
# Missing identifiers
# ---------------------------------------------------------------------------

class TestMissingIdentifiers:
    def test_paper_with_no_doi_or_arxiv_uses_title(self):
        existing = make_paper(title="Only Title No Identifiers")
        dedup = Deduplicator([existing])

        incoming = make_paper(title="Only Title No Identifiers")
        result = dedup.check(incoming)

        assert result.is_duplicate
        assert result.match_method == "title"

    def test_incoming_with_no_identifiers_uses_title(self):
        existing = make_paper(title="A Study", doi="10.1234/study")
        dedup = Deduplicator([existing])

        # Incoming has no DOI or arXiv — will fall through to title
        incoming = make_paper(title="A Study")
        result = dedup.check(incoming)

        assert result.is_duplicate
        assert result.match_method == "title"
