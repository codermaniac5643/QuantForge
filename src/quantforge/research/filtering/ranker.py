# src/quantforge/research/filtering/ranker.py
"""
Candidate ranker for the QuantForge research pipeline.

The ranker:
  1. Runs all four deterministic scorers on each paper.
  2. Assembles a PaperEvaluation with individual DimensionScores.
  3. Computes a weighted composite score.
  4. Sorts candidates by composite score (descending).
  5. Returns a RankedCandidate list with full score transparency.

Design principles (from AGENTS.md / ROADMAP.md):
  - Every score dimension is preserved separately — no silent aggregation.
  - Score weights are configurable; defaults live here as constants.
  - Version the scoring configuration so stored scores can be recomputed.
  - LLM scores are not mixed in here — this is deterministic only.
  - The ranker is a pure function over its inputs; it has no side effects.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from uuid import UUID

from quantforge.models.evaluation import DimensionScore, PaperEvaluation
from quantforge.models.paper import ResearchPaper
from quantforge.research.filtering.scorer import (
    score_credibility,
    score_implementability,
    score_relevance,
    score_reproducibility,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scoring configuration
# ---------------------------------------------------------------------------

# Scoring schema version — bump when weights or scorer logic changes so
# that stored scores can be identified as stale.
SCORING_VERSION = "1.0.0"

# Default composite weights.
# Must sum to 1.0.
DEFAULT_WEIGHTS: dict[str, float] = {
    "relevance": 0.35,
    "credibility": 0.20,
    "implementability": 0.25,
    "reproducibility": 0.15,
    "priority": 0.05,
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class RankedCandidate:
    """
    A paper together with its full scored evaluation and composite rank score.

    Attributes:
        paper:          The research paper.
        evaluation:     All dimension scores and composite.
        rank:           1-based rank position after sorting (1 = best).
        scoring_version: Version tag for the scoring schema used.
    """
    paper: ResearchPaper
    evaluation: PaperEvaluation
    rank: int = 0
    scoring_version: str = SCORING_VERSION

    @property
    def composite_score(self) -> float:
        return self.evaluation.composite_score or 0.0

    @property
    def relevance_score(self) -> float:
        return self.evaluation.relevance.score if self.evaluation.relevance else 0.0

    @property
    def credibility_score(self) -> float:
        return self.evaluation.credibility.score if self.evaluation.credibility else 0.0

    @property
    def implementability_score(self) -> float:
        d = self.evaluation.implementability
        return d.score if d else 0.0

    @property
    def reproducibility_score(self) -> float:
        d = self.evaluation.reproducibility
        return d.score if d else 0.0

    def score_summary(self) -> str:
        """Human-readable single-line score summary for logging and CLI."""
        return (
            f"composite={self.composite_score:.3f} "
            f"rel={self.relevance_score:.3f} "
            f"cred={self.credibility_score:.3f} "
            f"impl={self.implementability_score:.3f} "
            f"repr={self.reproducibility_score:.3f}"
        )


# ---------------------------------------------------------------------------
# Ranker
# ---------------------------------------------------------------------------

class CandidateRanker:
    """
    Scores and ranks a list of ResearchPaper objects.

    Usage:
        ranker = CandidateRanker()
        candidates = ranker.rank(papers, topic_keywords=["momentum", "trend"])
        # candidates[0] is the highest-scoring paper

    The ranker is stateless — create a new instance or reuse across batches.
    """

    def __init__(
        self,
        weights: dict[str, float] | None = None,
    ) -> None:
        """
        Args:
            weights: Optional scoring weights dict. Keys must be a subset of
                     {"relevance", "credibility", "implementability",
                      "reproducibility", "priority"}.
                     Defaults to DEFAULT_WEIGHTS.
        """
        self._weights = weights or DEFAULT_WEIGHTS
        _validate_weights(self._weights)

    def score_one(
        self,
        paper: ResearchPaper,
        topic_keywords: list[str] | None = None,
    ) -> RankedCandidate:
        """
        Score a single paper across all four deterministic dimensions.

        Returns a RankedCandidate with rank=0 (rank is set by rank()).
        """
        relevance = score_relevance(paper, topic_keywords=topic_keywords)
        credibility = score_credibility(paper)
        implementability = score_implementability(paper)
        reproducibility = score_reproducibility(paper)

        evaluation = PaperEvaluation(
            paper_id=paper.id,
            relevance=relevance,
            credibility=credibility,
            implementability=implementability,
            reproducibility=reproducibility,
            # priority score is left None here — it will be LLM-assisted (M6)
            # or set externally if topic-specific priority is applied.
        )
        evaluation.compute_composite(weights=self._weights)

        return RankedCandidate(
            paper=paper,
            evaluation=evaluation,
            scoring_version=SCORING_VERSION,
        )

    def rank(
        self,
        papers: list[ResearchPaper],
        topic_keywords: list[str] | None = None,
    ) -> list[RankedCandidate]:
        """
        Score all papers and return them sorted by composite score (best first).

        Papers that tie on composite score are sorted by relevance score,
        then by title (alphabetical) as a final stable tiebreaker.

        Args:
            papers:         Papers to score.
            topic_keywords: Optional list of topic-specific keywords forwarded
                            to the relevance scorer.

        Returns:
            Ranked list of RankedCandidate objects, rank attribute set (1-based).
        """
        if not papers:
            return []

        candidates: list[RankedCandidate] = []
        for paper in papers:
            try:
                candidate = self.score_one(paper, topic_keywords=topic_keywords)
                candidates.append(candidate)
            except Exception as e:
                logger.warning(
                    "Scoring failed for paper %s (%r): %s",
                    paper.id,
                    paper.title[:60],
                    e,
                )

        # Sort: composite desc → relevance desc → title asc (stable tiebreaker)
        candidates.sort(
            key=lambda c: (
                -c.composite_score,
                -c.relevance_score,
                c.paper.title.lower(),
            )
        )

        # Assign 1-based ranks
        for idx, candidate in enumerate(candidates, start=1):
            candidate.rank = idx

        logger.info(
            "Ranked %d papers. Top score: %.4f, bottom: %.4f",
            len(candidates),
            candidates[0].composite_score if candidates else 0,
            candidates[-1].composite_score if candidates else 0,
        )
        return candidates

    def rank_and_filter(
        self,
        papers: list[ResearchPaper],
        min_relevance: float = 0.0,
        min_composite: float = 0.0,
        topic_keywords: list[str] | None = None,
    ) -> tuple[list[RankedCandidate], list[tuple[ResearchPaper, str]]]:
        """
        Score, rank, and immediately filter below threshold.

        Returns:
            (accepted_candidates, rejected_list)
            rejected_list: list of (paper, reason_string) for papers below threshold.
        """
        all_ranked = self.rank(papers, topic_keywords=topic_keywords)
        accepted: list[RankedCandidate] = []
        rejected: list[tuple[ResearchPaper, str]] = []

        for candidate in all_ranked:
            reasons: list[str] = []
            if candidate.relevance_score < min_relevance:
                reasons.append(
                    f"relevance={candidate.relevance_score:.4f} < {min_relevance}"
                )
            if candidate.composite_score < min_composite:
                reasons.append(
                    f"composite={candidate.composite_score:.4f} < {min_composite}"
                )
            if reasons:
                rejected.append((candidate.paper, "; ".join(reasons)))
            else:
                accepted.append(candidate)

        logger.info(
            "rank_and_filter: %d accepted, %d filtered out",
            len(accepted),
            len(rejected),
        )
        return accepted, rejected


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _validate_weights(weights: dict[str, float]) -> None:
    """Raise ValueError if weights are invalid."""
    allowed_keys = {
        "relevance", "credibility", "implementability",
        "reproducibility", "priority",
    }
    unknown = set(weights) - allowed_keys
    if unknown:
        raise ValueError(
            f"Unknown weight keys: {unknown}. Allowed: {allowed_keys}"
        )
    if any(w < 0 for w in weights.values()):
        raise ValueError("All weights must be >= 0")

