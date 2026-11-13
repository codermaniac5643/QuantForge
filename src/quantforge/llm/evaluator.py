# src/quantforge/llm/evaluator.py

import json
import logging
from typing import Any

from quantforge.llm.budget import BudgetManager, BudgetExhaustedError, LLMDisabledError
from quantforge.llm.provider import LLMRequest, LLMProviderError
from quantforge.models.evaluation import PaperEvaluation, DimensionScore
from quantforge.models.paper import ResearchPaper

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a quantitative research evaluator for a systematic trading firm.
Your task is to assess the PRIORITY of a research paper for implementation.

Priority means: how valuable would it be to implement this strategy NOW given its novelty,
practical relevance, and gap it fills in a systematic trading toolkit.

Respond ONLY with a valid JSON object in this exact format:
{
    "score": <float between 0.0 and 1.0>,
    "rationale": "<one sentence explaining the score>",
    "evidence": ["<specific observation 1>", "<specific observation 2>"]
}

Do not include any text outside the JSON object."""


def _build_prompt(paper: ResearchPaper) -> str:
    """
    Build the evaluation prompt for a paper.
    Only sends title, abstract, and keywords — never the full PDF.
    Keeps token usage minimal.
    """
    authors = ", ".join(a.name for a in paper.authors[:5])
    keywords = ", ".join(paper.keywords[:10]) if paper.keywords else "none"
    date = str(paper.published_date) if paper.published_date else "unknown"

    abstract = paper.abstract or "No abstract available."
    # Truncate long abstracts to control token usage
    if len(abstract) > 2000:
        abstract = abstract[:2000] + "... [truncated]"

    return f"""Evaluate the PRIORITY of this research paper for a systematic trading firm.

Title: {paper.title}
Authors: {authors}
Published: {date}
Keywords: {keywords}

Abstract:
{abstract}

Assess how valuable it would be to implement this strategy now.
Consider: novelty, practical relevance, gap in existing toolkit, timing.
Respond with JSON only."""


def _parse_llm_output(content: str) -> dict[str, Any]:
    """
    Parse JSON from LLM response.
    Handles common issues like extra whitespace or markdown fences.
    """
    # Strip markdown code fences if present
    text = content.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(
            line for line in lines
            if not line.startswith("```")
        ).strip()
    return json.loads(text)


class LLMEvaluator:
    """
    Uses the LLM to fill the priority dimension on a PaperEvaluation.
    All calls go through BudgetManager — never directly to the provider.

    Only called for papers that passed deterministic filtering and scoring.
    Budget exhaustion is handled gracefully — evaluation continues without
    the priority score rather than failing.
    """

    def __init__(
        self,
        budget_manager: BudgetManager,
        max_tokens: int = 500,
    ) -> None:
        self._budget = budget_manager
        self._max_tokens = max_tokens

    async def evaluate_priority(
        self,
        paper: ResearchPaper,
        evaluation: PaperEvaluation,
    ) -> PaperEvaluation:
        """
        Fill the priority dimension on an existing PaperEvaluation.
        Returns the evaluation unchanged if LLM is disabled or budget exhausted.
        Never raises — failures are logged and the evaluation continues.
        """
        paper_id = str(paper.id)

        request = LLMRequest(
            system_prompt=SYSTEM_PROMPT,
            prompt=_build_prompt(paper),
            max_tokens=self._max_tokens,
            temperature=0.1,
        )

        try:
            response = await self._budget.call(
                request=request,
                task="priority_evaluation",
                paper_id=paper_id,
            )
            parsed = _parse_llm_output(response.content)

            score = float(parsed["score"])
            if not 0.0 <= score <= 1.0:
                raise ValueError(f"Score {score} out of range [0.0, 1.0]")

            evaluation.priority = DimensionScore(
                score=score,
                rationale=parsed.get("rationale"),
                evidence=parsed.get("evidence", []),
                llm_assisted=True,
            )

            logger.info(
                "LLM priority evaluation for paper %s: %.2f — %s",
                paper_id,
                score,
                parsed.get("rationale", ""),
            )

        except (LLMDisabledError, BudgetExhaustedError) as e:
            # Controlled stop — log and continue without priority score
            logger.info(
                "Skipping LLM evaluation for paper %s: %s",
                paper_id, e,
            )

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(
                "Failed to parse LLM priority response for paper %s: %s",
                paper_id, e,
            )

        except LLMProviderError as e:
            logger.error(
                "LLM provider error for paper %s: %s",
                paper_id, e,
            )

        return evaluation

    async def evaluate_batch(
        self,
        papers: list[ResearchPaper],
        evaluations: dict[str, PaperEvaluation],
    ) -> dict[str, PaperEvaluation]:
        """
        Evaluate priority for a batch of papers.
        Stops on budget exhaustion — does not skip ahead.
        Returns evaluations with whatever priority scores were filled.
        """
        for paper in papers:
            paper_id = str(paper.id)
            if paper_id not in evaluations:
                continue

            if self._budget.is_exhausted:
                logger.info(
                    "Budget exhausted — stopping batch at paper %s. "
                    "%d papers evaluated, %d remaining.",
                    paper_id,
                    sum(1 for e in evaluations.values() if e.priority is not None),
                    sum(1 for e in evaluations.values() if e.priority is None),
                )
                break

            evaluations[paper_id] = await self.evaluate_priority(
                paper=paper,
                evaluation=evaluations[paper_id],
            )

        return evaluations