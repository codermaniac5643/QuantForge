# src/quantforge/research/filtering/__init__.py

from quantforge.research.filtering.scorer import (
    score_relevance,
    score_credibility,
    score_implementability,
    score_reproducibility,
)
from quantforge.research.filtering.filters import (
    FilterResult,
    filter_by_date,
    filter_minimum_fields,
    filter_by_relevance_score,
    filter_by_composite_score,
    filter_duplicates,
    apply_pre_score_filters,
    apply_post_score_filters,
)
from quantforge.research.filtering.ranker import (
    CandidateRanker,
    RankedCandidate,
    DEFAULT_WEIGHTS,
    SCORING_VERSION,
)

__all__ = [
    # scorer
    "score_relevance",
    "score_credibility",
    "score_implementability",
    "score_reproducibility",
    # filters
    "FilterResult",
    "filter_by_date",
    "filter_minimum_fields",
    "filter_by_relevance_score",
    "filter_by_composite_score",
    "filter_duplicates",
    "apply_pre_score_filters",
    "apply_post_score_filters",
    # ranker
    "CandidateRanker",
    "RankedCandidate",
    "DEFAULT_WEIGHTS",
    "SCORING_VERSION",
]

