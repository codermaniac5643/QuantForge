# src/quantforge/models/evaluation.py

from datetime import datetime, timezone
from uuid import UUID
from pydantic import BaseModel, Field, field_validator


class DimensionScore(BaseModel):
    """
    A score along a single evaluation dimension.
    Every score must carry its rationale — a bare number is not enough.
    """
    score: float = Field(ge=0.0, le=1.0)
    rationale: str | None = None         # why this score was assigned
    evidence: list[str] = Field(default_factory=list)   # supporting quotes or facts
    llm_assisted: bool = False           # True if an LLM produced this score


class PaperEvaluation(BaseModel):
    """
    Full evaluation of a research paper across five dimensions.
    Scores are independent — a paper can be highly relevant
    but low on implementability (e.g. requires proprietary data).
    """

    paper_id: UUID
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # --- Five Evaluation Dimensions ---

    # Does this paper address a topic we care about?
    relevance: DimensionScore | None = None

    # Is this credible research? (journal, citations, methodology)
    credibility: DimensionScore | None = None

    # Can the strategy actually be implemented with available data?
    implementability: DimensionScore | None = None

    # Are the results reproducible? (data described, rules explicit)
    reproducibility: DimensionScore | None = None

    # Should we prioritize this now? (novelty, gap in our research)
    priority: DimensionScore | None = None

    # --- Composite Score ---
    # Weighted combination — computed, not manually assigned
    composite_score: float | None = None

    # Any notes about why evaluation is incomplete
    evaluation_notes: str | None = None

    def compute_composite(
        self,
        weights: dict[str, float] | None = None,
    ) -> float:
        """
        Compute a weighted composite score from available dimensions.
        Default weights can be overridden via config.
        Only scores dimensions that are present — missing ones are skipped.
        """
        default_weights = {
            "relevance": 0.35,
            "credibility": 0.20,
            "implementability": 0.25,
            "reproducibility": 0.15,
            "priority": 0.05,
        }
        w = weights or default_weights

        total_weight = 0.0
        weighted_sum = 0.0

        for dim, weight in w.items():
            dimension: DimensionScore | None = getattr(self, dim, None)
            if dimension is not None:
                weighted_sum += dimension.score * weight
                total_weight += weight

        if total_weight == 0:
            return 0.0

        self.composite_score = round(weighted_sum / total_weight, 4)
        return self.composite_score