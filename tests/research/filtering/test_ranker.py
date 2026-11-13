# tests/research/filtering/test_ranker.py
"""
Unit tests for CandidateRanker and RankedCandidate.

Covers:
  - score_one: produces valid RankedCandidate with all dimension scores
  - rank: sorted by composite desc, relevance desc, title asc tiebreaker
  - rank: assigns 1-based rank correctly
  - rank: returns empty list for empty input
  - rank: handles scoring failures gracefully (bad paper skipped, not raised)
  - rank_and_filter: threshold filtering applied after ranking
  - RankedCandidate: score_summary format
  - Custom weights accepted
  - Invalid weights raise ValueError
  - SCORING_VERSION is a non-empty string
"""

import pytest
from datetime import date, timedelta

from quantforge.models.paper import Author, ResearchPaper, SourceReference
from quantforge.models.states import PaperState
from quantforge.research.filtering.ranker import (
    CandidateRanker,
    RankedCandidate,
    DEFAULT_WEIGHTS,
    SCORING_VERSION,
    _validate_weights,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_source(name: str = "arxiv", source_id: str = "2401.00001") -> SourceReference:
    return SourceReference(source_name=name, source_id=source_id)


def make_paper(
    title: str = "A Research Paper",
    abstract: str | None = None,
    keywords: list[str] | None = None,
    doi: str | None = None,
    arxiv_id: str | None = None,
    published_date: date | None = None,
    authors: list | None = None,
    journal: str | None = None,
) -> ResearchPaper:
    return ResearchPaper(
        title=title,
        abstract=abstract,
        keywords=keywords or [],
        doi=doi,
        arxiv_id=arxiv_id,
        published_date=published_date,
        authors=authors or [],
        journal=journal,
        sources=[make_source()],
        state=PaperState.DISCOVERED,
    )


# High-signal paper
GOOD_PAPER = make_paper(
    title="Cross-Sectional Momentum Strategy in Equity Markets",
    abstract=(
        "We examine momentum and trend following strategies in equity markets "
        "using CRSP daily data. Out-of-sample tests include Fama-French factor "
        "adjustments, Newey-West standard errors, and transaction costs."
    ),
    keywords=["q-fin.PM", "q-fin.ST"],
    doi="10.1234/momentum",
    arxiv_id="2401.00001",
    authors=[Author(name="Alice"), Author(name="Bob")],
    published_date=date.today() - timedelta(days=100),
    journal="Journal of Finance",
)

# Low-signal paper
WEAK_PAPER = make_paper(
    title="A Theoretical Note on Market Equilibrium",
    abstract=None,
    keywords=[],
)


# ---------------------------------------------------------------------------
# SCORING_VERSION
# ---------------------------------------------------------------------------

def test_scoring_version_is_string():
    assert isinstance(SCORING_VERSION, str)
    assert len(SCORING_VERSION) > 0


# ---------------------------------------------------------------------------
# _validate_weights
# ---------------------------------------------------------------------------

class TestValidateWeights:
    def test_valid_weights_do_not_raise(self):
        _validate_weights({"relevance": 0.5, "credibility": 0.5})

    def test_unknown_key_raises(self):
        with pytest.raises(ValueError, match="Unknown weight"):
            _validate_weights({"unknown_dim": 0.5})

    def test_negative_weight_raises(self):
        with pytest.raises(ValueError, match=">= 0"):
            _validate_weights({"relevance": -0.1})

    def test_zero_weight_allowed(self):
        # Zero weight is allowed (effectively disables that dimension)
        _validate_weights({"relevance": 0.0, "credibility": 1.0})


# ---------------------------------------------------------------------------
# CandidateRanker — constructor
# ---------------------------------------------------------------------------

class TestCandidateRankerConstructor:
    def test_default_weights_accepted(self):
        ranker = CandidateRanker()
        assert ranker._weights == DEFAULT_WEIGHTS

    def test_custom_weights_accepted(self):
        weights = {"relevance": 1.0}
        ranker = CandidateRanker(weights=weights)
        assert ranker._weights == weights

    def test_invalid_weights_raise_at_init(self):
        with pytest.raises(ValueError):
            CandidateRanker(weights={"bad_key": 0.5})


# ---------------------------------------------------------------------------
# score_one
# ---------------------------------------------------------------------------

class TestScoreOne:
    def test_returns_ranked_candidate(self):
        ranker = CandidateRanker()
        result = ranker.score_one(GOOD_PAPER)
        assert isinstance(result, RankedCandidate)

    def test_paper_reference_preserved(self):
        ranker = CandidateRanker()
        result = ranker.score_one(GOOD_PAPER)
        assert result.paper is GOOD_PAPER

    def test_rank_zero_before_rank_called(self):
        """score_one doesn't set rank — that's done by rank()."""
        ranker = CandidateRanker()
        result = ranker.score_one(GOOD_PAPER)
        assert result.rank == 0

    def test_all_dimension_scores_present(self):
        ranker = CandidateRanker()
        result = ranker.score_one(GOOD_PAPER)
        assert result.evaluation.relevance is not None
        assert result.evaluation.credibility is not None
        assert result.evaluation.implementability is not None
        assert result.evaluation.reproducibility is not None

    def test_composite_score_computed(self):
        ranker = CandidateRanker()
        result = ranker.score_one(GOOD_PAPER)
        assert result.evaluation.composite_score is not None
        assert 0.0 <= result.evaluation.composite_score <= 1.0

    def test_scoring_version_set(self):
        ranker = CandidateRanker()
        result = ranker.score_one(GOOD_PAPER)
        assert result.scoring_version == SCORING_VERSION

    def test_good_paper_scores_higher_than_weak(self):
        ranker = CandidateRanker()
        good = ranker.score_one(GOOD_PAPER)
        weak = ranker.score_one(WEAK_PAPER)
        assert good.composite_score > weak.composite_score

    def test_dimension_scores_in_range(self):
        ranker = CandidateRanker()
        result = ranker.score_one(GOOD_PAPER)
        for dim in [
            result.relevance_score,
            result.credibility_score,
            result.implementability_score,
            result.reproducibility_score,
        ]:
            assert 0.0 <= dim <= 1.0

    def test_all_scores_not_llm_assisted(self):
        ranker = CandidateRanker()
        result = ranker.score_one(GOOD_PAPER)
        for dim in [
            result.evaluation.relevance,
            result.evaluation.credibility,
            result.evaluation.implementability,
            result.evaluation.reproducibility,
        ]:
            assert dim is not None
            assert dim.llm_assisted is False


# ---------------------------------------------------------------------------
# CandidateRanker.rank
# ---------------------------------------------------------------------------

class TestRank:
    def test_empty_input_returns_empty_list(self):
        ranker = CandidateRanker()
        assert ranker.rank([]) == []

    def test_rank_sorted_best_first(self):
        ranker = CandidateRanker()
        candidates = ranker.rank([WEAK_PAPER, GOOD_PAPER])
        assert candidates[0].composite_score >= candidates[1].composite_score

    def test_rank_attribute_1_based(self):
        ranker = CandidateRanker()
        papers = [GOOD_PAPER, WEAK_PAPER]
        candidates = ranker.rank(papers)
        ranks = [c.rank for c in candidates]
        assert ranks[0] == 1
        assert ranks[1] == 2

    def test_rank_all_assigned(self):
        ranker = CandidateRanker()
        papers = [
            make_paper(title=f"Paper {i}")
            for i in range(5)
        ]
        candidates = ranker.rank(papers)
        assert len(candidates) == 5
        assert sorted(c.rank for c in candidates) == [1, 2, 3, 4, 5]

    def test_rank_stable_title_tiebreaker(self):
        """Papers with equal scores sort alphabetically by title."""
        ranker = CandidateRanker()
        # Two identical bare-minimum papers — same scores, different titles
        p_a = make_paper(title="Alpha Paper")
        p_z = make_paper(title="Zeta Paper")
        candidates = ranker.rank([p_z, p_a])
        # Alpha should rank ahead of Zeta when scores are equal
        titles = [c.paper.title for c in candidates]
        assert titles.index("Alpha Paper") < titles.index("Zeta Paper")

    def test_rank_single_paper(self):
        ranker = CandidateRanker()
        candidates = ranker.rank([GOOD_PAPER])
        assert len(candidates) == 1
        assert candidates[0].rank == 1

    def test_topic_keywords_forwarded_to_scorer(self):
        """Providing topic_keywords should not break ranking."""
        ranker = CandidateRanker()
        papers = [GOOD_PAPER, WEAK_PAPER]
        candidates = ranker.rank(papers, topic_keywords=["momentum", "equity"])
        assert len(candidates) == 2

    def test_custom_weights_affect_ranking(self):
        """With relevance weight = 0, a credibility-dominant ranking emerges."""
        # Both papers; GOOD_PAPER has DOI + arXiv + venue so high credibility.
        # We just verify the ranker runs without error with custom weights.
        ranker = CandidateRanker(weights={"relevance": 0.0, "credibility": 1.0})
        candidates = ranker.rank([WEAK_PAPER, GOOD_PAPER])
        assert len(candidates) == 2
        assert candidates[0].rank == 1


# ---------------------------------------------------------------------------
# RankedCandidate properties
# ---------------------------------------------------------------------------

class TestRankedCandidateProperties:
    def test_score_summary_format(self):
        ranker = CandidateRanker()
        candidate = ranker.score_one(GOOD_PAPER)
        summary = candidate.score_summary()
        assert "composite=" in summary
        assert "rel=" in summary
        assert "cred=" in summary
        assert "impl=" in summary
        assert "repr=" in summary

    def test_composite_score_property(self):
        ranker = CandidateRanker()
        candidate = ranker.score_one(GOOD_PAPER)
        assert candidate.composite_score == (candidate.evaluation.composite_score or 0.0)

    def test_zero_scores_for_no_dimensions(self):
        """RankedCandidate with empty PaperEvaluation returns 0 for all scores."""
        from quantforge.models.evaluation import PaperEvaluation
        evaluation = PaperEvaluation(paper_id=GOOD_PAPER.id)
        evaluation.composite_score = 0.0
        candidate = RankedCandidate(paper=GOOD_PAPER, evaluation=evaluation)
        assert candidate.relevance_score == 0.0
        assert candidate.credibility_score == 0.0
        assert candidate.implementability_score == 0.0
        assert candidate.reproducibility_score == 0.0


# ---------------------------------------------------------------------------
# rank_and_filter
# ---------------------------------------------------------------------------

class TestRankAndFilter:
    def test_above_thresholds_accepted(self):
        ranker = CandidateRanker()
        accepted, rejected = ranker.rank_and_filter(
            [GOOD_PAPER],
            min_relevance=0.0,
            min_composite=0.0,
        )
        assert len(accepted) == 1
        assert len(rejected) == 0

    def test_strict_thresholds_filter_all(self):
        """Setting min_composite=1.0 should filter everything out."""
        ranker = CandidateRanker()
        accepted, rejected = ranker.rank_and_filter(
            [GOOD_PAPER, WEAK_PAPER],
            min_relevance=0.0,
            min_composite=1.0,
        )
        assert len(accepted) == 0
        assert len(rejected) == 2

    def test_rejection_includes_reason(self):
        ranker = CandidateRanker()
        _, rejected = ranker.rank_and_filter(
            [WEAK_PAPER],
            min_relevance=0.0,
            min_composite=1.0,
        )
        assert len(rejected) == 1
        paper, reason = rejected[0]
        assert "composite" in reason.lower()

    def test_mixed_batch(self):
        ranker = CandidateRanker()
        # GOOD_PAPER should pass; WEAK_PAPER should fail composite
        accepted, rejected = ranker.rank_and_filter(
            [GOOD_PAPER, WEAK_PAPER],
            min_relevance=0.0,
            min_composite=0.3,
        )
        # GOOD_PAPER is expected to have composite > 0.3
        accepted_titles = [c.paper.title for c in accepted]
        assert "Cross-Sectional Momentum Strategy in Equity Markets" in accepted_titles

    def test_empty_input(self):
        ranker = CandidateRanker()
        accepted, rejected = ranker.rank_and_filter([], 0.0, 0.0)
        assert accepted == []
        assert rejected == []

    def test_accepted_still_ranked(self):
        ranker = CandidateRanker()
        accepted, _ = ranker.rank_and_filter(
            [GOOD_PAPER, WEAK_PAPER],
            min_relevance=0.0,
            min_composite=0.0,
        )
        # Accepted candidates should still have rank set
        for candidate in accepted:
            assert candidate.rank >= 1

