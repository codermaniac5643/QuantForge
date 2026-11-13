# tests/research/filtering/test_filters.py
"""
Unit tests for the hard-cutoff filter functions.

Covers:
  - filter_by_date: within window, too old, missing date
  - filter_minimum_fields: missing title, missing sources
  - filter_by_relevance_score: above/below threshold, missing score
  - filter_by_composite_score: above/below threshold, missing score
  - filter_duplicates: unique UUIDs, repeated UUID
  - apply_pre_score_filters: combined pipeline
  - apply_post_score_filters: combined scored pipeline
  - FilterResult properties: n_accepted, n_rejected, summary
"""

import pytest
from datetime import date, timedelta
from uuid import uuid4

from quantforge.models.paper import ResearchPaper, SourceReference
from quantforge.models.states import PaperState
from quantforge.research.filtering.filters import (
    FilterResult,
    filter_by_date,
    filter_by_composite_score,
    filter_by_relevance_score,
    filter_duplicates,
    filter_minimum_fields,
    apply_pre_score_filters,
    apply_post_score_filters,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_source(name: str = "arxiv", source_id: str = "2401.00001") -> SourceReference:
    return SourceReference(source_name=name, source_id=source_id)


def make_paper(
    title: str = "A Research Paper",
    published_date: date | None = None,
    sources: list | None = None,
) -> ResearchPaper:
    return ResearchPaper(
        title=title,
        published_date=published_date,
        sources=sources if sources is not None else [make_source()],
        state=PaperState.DISCOVERED,
    )


def attach_scores(paper: ResearchPaper, relevance: float, composite: float) -> ResearchPaper:
    """Attach score attributes used by the score-based filters."""
    object.__setattr__(paper, "_relevance_score", relevance)
    object.__setattr__(paper, "_composite_score", composite)
    return paper


# ---------------------------------------------------------------------------
# FilterResult
# ---------------------------------------------------------------------------

class TestFilterResult:
    def test_n_accepted(self):
        r = FilterResult(filter_name="test")
        p = make_paper()
        r.accepted = [p, p]
        assert r.n_accepted == 2

    def test_n_rejected(self):
        r = FilterResult(filter_name="test")
        p = make_paper()
        r.rejected = [(p, "reason")]
        assert r.n_rejected == 1

    def test_summary_contains_filter_name(self):
        r = FilterResult(filter_name="my_filter")
        r.accepted = [make_paper()]
        assert "my_filter" in r.summary()

    def test_summary_contains_counts(self):
        r = FilterResult(filter_name="f")
        r.accepted = [make_paper(), make_paper()]
        r.rejected = [(make_paper(), "x")]
        summary = r.summary()
        assert "2" in summary  # accepted
        assert "1" in summary  # rejected


# ---------------------------------------------------------------------------
# filter_by_date
# ---------------------------------------------------------------------------

class TestFilterByDate:
    def test_recent_paper_accepted(self):
        paper = make_paper(published_date=date.today() - timedelta(days=30))
        result = filter_by_date([paper], lookback_days=365)
        assert len(result.accepted) == 1
        assert len(result.rejected) == 0

    def test_old_paper_rejected(self):
        paper = make_paper(published_date=date(2010, 1, 1))
        result = filter_by_date([paper], lookback_days=365)
        assert len(result.accepted) == 0
        assert len(result.rejected) == 1

    def test_paper_on_cutoff_boundary_accepted(self):
        cutoff = date.today() - timedelta(days=365)
        paper = make_paper(published_date=cutoff)
        result = filter_by_date([paper], lookback_days=365)
        assert len(result.accepted) == 1

    def test_paper_with_no_date_accepted(self):
        """Papers with unknown dates must not be rejected."""
        paper = make_paper(published_date=None)
        result = filter_by_date([paper], lookback_days=365)
        assert len(result.accepted) == 1

    def test_mixed_batch(self):
        recent = make_paper(title="Recent", published_date=date.today() - timedelta(days=10))
        old = make_paper(title="Old", published_date=date(2015, 6, 1))
        no_date = make_paper(title="NoDate", published_date=None)
        result = filter_by_date([recent, old, no_date], lookback_days=180)
        assert len(result.accepted) == 2  # recent + no_date
        assert len(result.rejected) == 1

    def test_rejection_reason_mentions_date(self):
        paper = make_paper(published_date=date(2010, 1, 1))
        result = filter_by_date([paper], lookback_days=365)
        _, reason = result.rejected[0]
        assert "2010" in reason or "lookback" in reason.lower()

    def test_empty_input(self):
        result = filter_by_date([], lookback_days=365)
        assert result.n_accepted == 0
        assert result.n_rejected == 0

    def test_filter_name_set(self):
        result = filter_by_date([], lookback_days=365)
        assert result.filter_name == "date_filter"


# ---------------------------------------------------------------------------
# filter_minimum_fields
# ---------------------------------------------------------------------------

class TestFilterMinimumFields:
    def test_complete_paper_accepted(self):
        paper = make_paper(title="A Study", sources=[make_source()])
        result = filter_minimum_fields([paper])
        assert result.n_accepted == 1

    def test_missing_title_rejected(self):
        paper = make_paper(title="   ", sources=[make_source()])
        result = filter_minimum_fields([paper])
        assert result.n_rejected == 1
        assert "title" in result.rejected[0][1].lower()

    def test_missing_sources_rejected(self):
        paper = make_paper(title="A Study", sources=[])
        result = filter_minimum_fields([paper])
        assert result.n_rejected == 1
        assert "source" in result.rejected[0][1].lower()

    def test_empty_input(self):
        result = filter_minimum_fields([])
        assert result.n_accepted == 0
        assert result.n_rejected == 0

    def test_filter_name_set(self):
        result = filter_minimum_fields([])
        assert result.filter_name == "minimum_fields_filter"


# ---------------------------------------------------------------------------
# filter_by_relevance_score
# ---------------------------------------------------------------------------

class TestFilterByRelevanceScore:
    def test_above_threshold_accepted(self):
        paper = make_paper()
        attach_scores(paper, relevance=0.6, composite=0.5)
        result = filter_by_relevance_score([paper], min_score=0.3)
        assert result.n_accepted == 1

    def test_below_threshold_rejected(self):
        paper = make_paper()
        attach_scores(paper, relevance=0.2, composite=0.5)
        result = filter_by_relevance_score([paper], min_score=0.3)
        assert result.n_rejected == 1

    def test_exactly_at_threshold_accepted(self):
        paper = make_paper()
        attach_scores(paper, relevance=0.3, composite=0.5)
        result = filter_by_relevance_score([paper], min_score=0.3)
        assert result.n_accepted == 1

    def test_no_score_rejected(self):
        paper = make_paper()
        result = filter_by_relevance_score([paper], min_score=0.3)
        assert result.n_rejected == 1
        _, reason = result.rejected[0]
        assert "scorer" in reason.lower()

    def test_rejection_reason_mentions_score(self):
        paper = make_paper()
        attach_scores(paper, relevance=0.1, composite=0.5)
        result = filter_by_relevance_score([paper], min_score=0.3)
        _, reason = result.rejected[0]
        assert "0.1" in reason or "relevance" in reason.lower()


# ---------------------------------------------------------------------------
# filter_by_composite_score
# ---------------------------------------------------------------------------

class TestFilterByCompositeScore:
    def test_above_threshold_accepted(self):
        paper = make_paper()
        attach_scores(paper, relevance=0.5, composite=0.7)
        result = filter_by_composite_score([paper], min_score=0.5)
        assert result.n_accepted == 1

    def test_below_threshold_rejected(self):
        paper = make_paper()
        attach_scores(paper, relevance=0.5, composite=0.3)
        result = filter_by_composite_score([paper], min_score=0.5)
        assert result.n_rejected == 1

    def test_no_score_rejected(self):
        paper = make_paper()
        result = filter_by_composite_score([paper], min_score=0.5)
        assert result.n_rejected == 1

    def test_rejection_reason_mentions_composite(self):
        paper = make_paper()
        attach_scores(paper, relevance=0.5, composite=0.2)
        result = filter_by_composite_score([paper], min_score=0.5)
        _, reason = result.rejected[0]
        assert "composite" in reason.lower()


# ---------------------------------------------------------------------------
# filter_duplicates
# ---------------------------------------------------------------------------

class TestFilterDuplicates:
    def test_unique_papers_all_accepted(self):
        papers = [make_paper(title="Paper A"), make_paper(title="Paper B")]
        result = filter_duplicates(papers)
        assert result.n_accepted == 2
        assert result.n_rejected == 0

    def test_duplicate_uuid_second_rejected(self):
        p1 = make_paper(title="Paper A")
        p2 = make_paper(title="Paper B")
        # Force p2 to have the same UUID as p1
        object.__setattr__(p2, "id", p1.id)
        result = filter_duplicates([p1, p2])
        assert result.n_accepted == 1
        assert result.n_rejected == 1

    def test_duplicate_rejection_reason_mentions_uuid(self):
        p1 = make_paper(title="Paper A")
        p2 = make_paper(title="Paper B")
        object.__setattr__(p2, "id", p1.id)
        result = filter_duplicates([p1, p2])
        _, reason = result.rejected[0]
        assert str(p1.id) in reason

    def test_empty_input(self):
        result = filter_duplicates([])
        assert result.n_accepted == 0

    def test_filter_name_set(self):
        result = filter_duplicates([])
        assert "duplicate" in result.filter_name.lower()


# ---------------------------------------------------------------------------
# apply_pre_score_filters  (combined pipeline)
# ---------------------------------------------------------------------------

class TestApplyPreScoreFilters:
    def test_valid_paper_passes_all_filters(self):
        paper = make_paper(
            title="A Valid Paper",
            published_date=date.today() - timedelta(days=10),
            sources=[make_source()],
        )
        result = apply_pre_score_filters([paper], lookback_days=365)
        assert result.n_accepted == 1
        assert result.n_rejected == 0

    def test_old_paper_filtered_out(self):
        old = make_paper(
            title="Old Paper",
            published_date=date(2010, 1, 1),
            sources=[make_source()],
        )
        result = apply_pre_score_filters([old], lookback_days=365)
        assert result.n_accepted == 0
        assert result.n_rejected == 1

    def test_no_sources_filtered_out(self):
        paper = make_paper(title="No Sources", sources=[])
        result = apply_pre_score_filters([paper], lookback_days=365)
        assert result.n_rejected == 1

    def test_mixed_batch(self):
        good = make_paper(
            title="Good Paper",
            published_date=date.today() - timedelta(days=5),
            sources=[make_source()],
        )
        bad_date = make_paper(
            title="Old Paper",
            published_date=date(2000, 1, 1),
            sources=[make_source()],
        )
        no_sources = make_paper(title="No Sources", sources=[])
        result = apply_pre_score_filters([good, bad_date, no_sources], lookback_days=365)
        assert result.n_accepted == 1
        assert result.n_rejected == 2

    def test_combined_filter_name(self):
        result = apply_pre_score_filters([], lookback_days=365)
        assert "pre_score" in result.filter_name.lower()

    def test_empty_input(self):
        result = apply_pre_score_filters([], lookback_days=365)
        assert result.n_accepted == 0


# ---------------------------------------------------------------------------
# apply_post_score_filters  (combined scored pipeline)
# ---------------------------------------------------------------------------

class TestApplyPostScoreFilters:
    def _scored(self, paper, rel, comp):
        return (paper, rel, comp)

    def test_above_both_thresholds_accepted(self):
        p = make_paper()
        result = apply_post_score_filters(
            [p],
            scored_papers=[self._scored(p, 0.5, 0.6)],
            min_relevance=0.3,
            min_composite=0.5,
        )
        assert result.n_accepted == 1

    def test_below_relevance_rejected(self):
        p = make_paper()
        result = apply_post_score_filters(
            [p],
            scored_papers=[self._scored(p, 0.1, 0.6)],
            min_relevance=0.3,
            min_composite=0.5,
        )
        assert result.n_rejected == 1
        assert "relevance" in result.rejected[0][1].lower()

    def test_below_composite_rejected(self):
        p = make_paper()
        result = apply_post_score_filters(
            [p],
            scored_papers=[self._scored(p, 0.5, 0.2)],
            min_relevance=0.3,
            min_composite=0.5,
        )
        assert result.n_rejected == 1
        assert "composite" in result.rejected[0][1].lower()

    def test_mixed_batch(self):
        p_good = make_paper(title="Good")
        p_low_rel = make_paper(title="Low Relevance")
        p_low_comp = make_paper(title="Low Composite")
        result = apply_post_score_filters(
            [p_good, p_low_rel, p_low_comp],
            scored_papers=[
                self._scored(p_good, 0.6, 0.7),
                self._scored(p_low_rel, 0.1, 0.7),
                self._scored(p_low_comp, 0.6, 0.2),
            ],
            min_relevance=0.3,
            min_composite=0.5,
        )
        assert result.n_accepted == 1
        assert result.n_rejected == 2

    def test_zero_thresholds_all_accepted(self):
        papers = [make_paper(title=f"Paper {i}") for i in range(5)]
        scored = [(p, 0.0, 0.0) for p in papers]
        result = apply_post_score_filters(papers, scored, 0.0, 0.0)
        assert result.n_accepted == 5

    def test_empty_input(self):
        result = apply_post_score_filters([], [], 0.3, 0.5)
        assert result.n_accepted == 0

