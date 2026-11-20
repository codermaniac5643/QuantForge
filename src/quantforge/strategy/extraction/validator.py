
# src/quantforge/strategy/extraction/validator.py

"""
Call 3 — Validation and Assessment.

Validates all extracted rule objects, produces the methodology assessment,
and checks for cross-pillar leakage.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from quantforge.llm.budget import BudgetManager, BudgetExhaustedError
from quantforge.llm.provider import LLMRequest, LLMProviderError
from quantforge.llm.prompts.strategy.validation import (
    SYSTEM_PROMPT,
    build_validation_prompt,
)

logger = logging.getLogger(__name__)


@dataclass
class LeakageFinding:
    """A rule found in the wrong pillar."""
    found_in_pillar: str
    rule_id: str
    should_be_in_pillar: str
    reason: str


@dataclass
class FlaggedRule:
    """A rule flagged for a validation issue."""
    pillar: str
    rule_id: str
    issue: str


@dataclass
class ValidationResult:
    """Output of Call 3."""
    completeness_class: str = "research_only"
    methodology_assessment: dict = field(default_factory=dict)
    flagged_rules: list[FlaggedRule] = field(default_factory=list)
    leakage_findings: list[LeakageFinding] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    success: bool = True
    error: str | None = None
    skipped_budget: bool = False


def _parse_validation_response(content: str) -> dict:
    """Parse JSON from validation response. Strips markdown fences."""
    text = content.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(
            line for line in lines
            if not line.startswith("```")
        ).strip()
    return json.loads(text)


def _default_assessment() -> dict:
    """Fallback methodology assessment when validation call fails."""
    return {
        "implementability": "research_only",
        "suitable_for_backtest": False,
        "primary_strength": "Not assessed — validation call failed.",
        "primary_weakness": "Not assessed — validation call failed.",
        "key_missing_components": [],
        "reason": "Validation call failed or was skipped due to budget exhaustion.",
    }


async def validate_extraction(
    extracted_pillars: dict,
    missing_components: list[dict],
    not_found_pillars: list[str],
    budget_manager: BudgetManager,
    max_tokens: int = 3000,
    paper_id: str | None = None,
) -> ValidationResult:
    """
    Call 3 — validate extracted rules and produce methodology assessment.

    Args:
        extracted_pillars: All pillar data from Call 2.
        missing_components: Absent/partial pillar records.
        not_found_pillars: Pillars with no sections from router.
        budget_manager: LLM budget manager.
        max_tokens: Output token budget.
        paper_id: For per-paper budget tracking.

    Returns:
        ValidationResult with completeness class, assessment, and findings.
    """
    result = ValidationResult()

    prompt = build_validation_prompt(
        extracted_pillars=extracted_pillars,
        missing_components=missing_components,
        not_found_pillars=not_found_pillars,
    )

    request = LLMRequest(
        system_prompt=SYSTEM_PROMPT,
        prompt=prompt,
        max_tokens=max_tokens,
        temperature=0.1,
    )

    # One retry on malformed JSON
    for attempt in range(1, 3):
        try:
            response = await budget_manager.call(
                request=request,
                task="strategy_validation",
                paper_id=paper_id,
            )
            result.input_tokens = response.input_tokens
            result.output_tokens = response.output_tokens

            parsed = _parse_validation_response(response.content)

            result.completeness_class = parsed.get(
                "completeness_class", "research_only"
            )
            result.methodology_assessment = parsed.get(
                "methodology_assessment", _default_assessment()
            )

            # Parse flagged rules
            for item in parsed.get("flagged_rules", []):
                result.flagged_rules.append(FlaggedRule(
                    pillar=item.get("pillar", ""),
                    rule_id=item.get("rule_id", ""),
                    issue=item.get("issue", ""),
                ))

            # Parse leakage findings
            for item in parsed.get("leakage_findings", []):
                result.leakage_findings.append(LeakageFinding(
                    found_in_pillar=item.get("found_in_pillar", ""),
                    rule_id=item.get("rule_id", ""),
                    should_be_in_pillar=item.get("should_be_in_pillar", ""),
                    reason=item.get("reason", ""),
                ))

            if result.leakage_findings:
                logger.warning(
                    "Validation: %d cross-pillar leakage finding(s) detected",
                    len(result.leakage_findings),
                )
            if result.flagged_rules:
                logger.warning(
                    "Validation: %d rule(s) flagged for review",
                    len(result.flagged_rules),
                )

            logger.info(
                "Validation complete — class=%r implementability=%r leakage=%d flagged=%d",
                result.completeness_class,
                result.methodology_assessment.get("implementability"),
                len(result.leakage_findings),
                len(result.flagged_rules),
            )
            result.success = True
            return result

        except json.JSONDecodeError as e:
            if attempt == 1:
                logger.warning(
                    "Validation: malformed JSON on attempt %d — retrying: %s",
                    attempt, e,
                )
                continue
            logger.error("Validation: malformed JSON after retry")
            result.success = False
            result.error = f"Malformed JSON after retry: {e}"
            result.methodology_assessment = _default_assessment()
            return result

        except BudgetExhaustedError as e:
            logger.warning("Validation: budget exhausted — skipping validation call")
            result.success = False
            result.skipped_budget = True
            result.error = str(e)
            result.methodology_assessment = _default_assessment()
            return result

        except LLMProviderError as e:
            logger.error("Validation: LLM provider error: %s", e)
            result.success = False
            result.error = str(e)
            result.methodology_assessment = _default_assessment()
            return result

        except Exception as e:
            logger.error("Validation: unexpected error: %s", e)
            result.success = False
            result.error = str(e)
            result.methodology_assessment = _default_assessment()
            return result

    return result