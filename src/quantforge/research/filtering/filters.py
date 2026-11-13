# src/quantforge/research/filtering/filters.py
"""
Hard-cutoff filters for the QuantForge research pipeline.

Filters are deterministic and pure — they never modify papers,
never call an LLM, and never touch the database.

Each filter function receives a list[ResearchPaper] and returns a
FilterResult that separates accepted papers from rejected ones,
carrying a per-paper reason for every rejection.

Design:
  - Filters are applied before scoring (date, minimum-field checks)
    or after scoring (score threshold, acquisition eligibility).
  - Rejections are logged at DEBUG level for traceability.
  - Accepted papers preserve their original order.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from quantforge.models.paper import ResearchPaper

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class FilterResult:
    """
    Output of a filter pass.

    Attributes:
        accepted:  Papers that passed the filter.
        rejected:  Papers that were removed, with per-paper reasons.
        filter_name: Which filter produced this result.
    """
    filter_name: str
    accepted: list[ResearchPaper] = field(default_factory=list)
    rejected: list[tuple[ResearchPaper, str]] = field(default_factory=list)

    @property
    def n_accepted(self) -> int:
        return len(self.accepted)

    @property
    def n_rejected(self) -> int:
        return len(self.rejected)

    def summary(self) -> str:
        return (
            f"{self.filter_name}: "
            f"{self.n_accepted} accepted, {self.n_rejected} rejected"
        )


# ---------------------------------------------------------------------------
# Individual filters
# ---------------------------------------------------------------------------

def filter_by_date(
    papers: list[ResearchPaper],
    lookback_days: int,
) -> FilterResult:
    """
    Remove papers with a publication date older than *lookback_days* ago.

    Papers with no publication date are **accepted** — we do not reject
    a paper merely because its date is missing, as it may still be recent.
    """
    result = FilterResult(filter_name="date_filter")
    cutoff = date.today() - timedelta(days=lookback_days)

    for paper in papers:
        if paper.published_date is None:
            result.accepted.append(paper)
        elif paper.published_date >= cutoff:
            result.accepted.append(paper)
        else:
            reason = (
                f"Published {paper.published_date} is older than "
                f"lookback cutoff {cutoff} ({lookback_days} days)"
            )
            logger.debug("Filtered out %r: %s", paper.title[:60], reason)
            result.rejected.append((paper, reason))

    return result


def filter_minimum_fields(papers: list[ResearchPaper]) -> FilterResult:
    """
    Remove papers that lack both a title and any source reference.

    A paper with no title cannot be identified, displayed, or deduplicated.
    A paper with no sources has no provenance.

    These are baseline sanity checks — not quality filters.
    """
    result = FilterResult(filter_name="minimum_fields_filter")

    for paper in papers:
        reasons: list[str] = []
        if not paper.title or not paper.title.strip():
            reasons.append("missing title")
        if not paper.sources:
            reasons.append("no source references")

        if reasons:
            reason_str = "; ".join(reasons)
            logger.debug("Filtered out paper %s: %s", paper.id, reason_str)
            result.rejected.append((paper, reason_str))
        else:
            result.accepted.append(paper)

    return result


def filter_by_relevance_score(
    papers: list[ResearchPaper],
    min_score: float,
) -> FilterResult:
    """
    Remove papers whose relevance score is below *min_score*.

    Papers with no relevance score yet (None) are also rejected — they
    must be scored before passing this filter. This enforces that the
    scoring step runs before the threshold check.
    """
    result = FilterResult(filter_name=f"relevance_score_filter(min={min_score})")

    for paper in papers:
        # Retrieve the score from the evaluation attached to the paper.
        # Scorer stores scores on PaperEvaluation objects; callers typically
        # attach the score directly to the paper's evaluation dict.
        # Here we look for a simple float on a parallel attribute; callers
        # that use the full PaperEvaluation workflow should extract the
        # composite_score themselves.
        score = _get_relevance_score(paper)

        if score is None:
            result.rejected.append(
                (paper, "relevance_score not computed — run scorer first")
            )
        elif score < min_score:
            result.rejected.append(
                (paper, f"relevance_score={score:.4f} below threshold {min_score}")
            )
        else:
            result.accepted.append(paper)

    return result


def filter_by_composite_score(
    papers: list[ResearchPaper],
    min_score: float,
) -> FilterResult:
    """
    Remove papers whose composite (overall) score is below *min_score*.

    Used to produce the acquisition-eligible shortlist.
    Papers with no composite score are rejected.
    """
    result = FilterResult(filter_name=f"composite_score_filter(min={min_score})")

    for paper in papers:
        score = _get_composite_score(paper)

        if score is None:
            result.rejected.append(
                (paper, "composite_score not computed — run scorer first")
            )
        elif score < min_score:
            result.rejected.append(
                (paper, f"composite_score={score:.4f} below threshold {min_score}")
            )
        else:
            result.accepted.append(paper)

    return result


def filter_duplicates(
    papers: list[ResearchPaper],
) -> FilterResult:
    """
    Remove papers that share a UUID with an earlier paper in *papers*.

    This is a lightweight guard against accidental duplicate entries in
    a batch. It does **not** replace the full deduplication service
    (Milestone 4) which checks across the whole database.
    """
    result = FilterResult(filter_name="in_batch_duplicate_filter")
    seen_ids: set = set()

    for paper in papers:
        if paper.id in seen_ids:
            result.rejected.append(
                (paper, f"duplicate UUID {paper.id} already seen in this batch")
            )
        else:
            seen_ids.add(paper.id)
            result.accepted.append(paper)

    return result


# ---------------------------------------------------------------------------
# Pipeline convenience helper
# ---------------------------------------------------------------------------

def apply_pre_score_filters(
    papers: list[ResearchPaper],
    lookback_days: int,
) -> FilterResult:
    """
    Apply all filters that run *before* scoring.

    Order: minimum-fields → date → in-batch duplicates.

    Returns a combined FilterResult where `accepted` are papers ready
    for the scorer.
    """
    all_rejected: list[tuple[ResearchPaper, str]] = []

    r1 = filter_minimum_fields(papers)
    all_rejected.extend(r1.rejected)

    r2 = filter_by_date(r1.accepted, lookback_days)
    all_rejected.extend(r2.rejected)

    r3 = filter_duplicates(r2.accepted)
    all_rejected.extend(r3.rejected)

    combined = FilterResult(filter_name="pre_score_pipeline")
    combined.accepted = r3.accepted
    combined.rejected = all_rejected

    logger.info(
        "Pre-score filters: %d accepted, %d rejected from %d papers",
        combined.n_accepted,
        combined.n_rejected,
        len(papers),
    )
    return combined


def apply_post_score_filters(
    papers: list[ResearchPaper],
    scored_papers: list[tuple[ResearchPaper, float, float]],
    min_relevance: float,
    min_composite: float,
) -> FilterResult:
    """
    Apply score-based filters after scoring.

    *scored_papers* is a list of (paper, relevance_score, composite_score)
    tuples produced by the ranker.

    Returns a FilterResult where `accepted` are acquisition-eligible candidates.
    """
    all_rejected: list[tuple[ResearchPaper, str]] = []
    accepted: list[ResearchPaper] = []

    for paper, rel_score, comp_score in scored_papers:
        reasons: list[str] = []
        if rel_score < min_relevance:
            reasons.append(
                f"relevance_score={rel_score:.4f} < {min_relevance}"
            )
        if comp_score < min_composite:
            reasons.append(
                f"composite_score={comp_score:.4f} < {min_composite}"
            )

        if reasons:
            all_rejected.append((paper, "; ".join(reasons)))
        else:
            accepted.append(paper)

    combined = FilterResult(filter_name="post_score_pipeline")
    combined.accepted = accepted
    combined.rejected = all_rejected

    logger.info(
        "Post-score filters: %d candidates, %d below threshold, from %d scored papers",
        combined.n_accepted,
        combined.n_rejected,
        len(scored_papers),
    )
    return combined


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _get_relevance_score(paper: ResearchPaper) -> float | None:
    """
    Extract the relevance score stored on the paper.

    The scorer workflow stores scores via PaperEvaluation; callers
    typically pass the score alongside the paper. This helper supports
    the case where a score has been persisted in a lightweight dict
    attribute for testing purposes.

    Returns None if no score is found.
    """
    # Support optional _relevance_score attribute set by the ranker/tests
    return getattr(paper, "_relevance_score", None)


def _get_composite_score(paper: ResearchPaper) -> float | None:
    """
    Extract the composite score stored on the paper.
    Returns None if no score is found.
    """
    return getattr(paper, "_composite_score", None)

