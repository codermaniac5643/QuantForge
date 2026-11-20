# src/quantforge/strategy/extraction/extractor.py

"""
Call 2 — Pillar Extractor.

Groups pillars into logical extraction groups and sends one LLM call
per group, using only the sections the router identified as relevant.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from quantforge.llm.budget import BudgetManager, BudgetExhaustedError
from quantforge.llm.provider import LLMRequest, LLMProviderError
from quantforge.llm.prompts.strategy.extraction import (
    SYSTEM_PROMPT,
    build_extraction_prompt,
)
from quantforge.strategy.extraction.chunker import Section

logger = logging.getLogger(__name__)

# Pillar groups — order matters: highest priority groups first
# so budget exhaustion drops lower-priority groups last
DEFAULT_PILLAR_GROUPS: dict[str, list[str]] = {
    "entry_exit_regime": [
        "market_regime",
        "market_eligibility",
        "entry_signal",
        "exit",
    ],
    "sizing_risk_portfolio": [
        "position_sizing",
        "risk_management",
        "portfolio",
    ],
    "asset_selection": [
        "asset_selection",
    ],
    "backtesting": [
        "backtesting",
    ],
}


@dataclass
class ExtractionGroupResult:
    """Result for one pillar group extraction call."""
    group_name: str
    pillars: list[str]
    extracted: dict = field(default_factory=dict)   # pillar_name -> pillar data
    input_tokens: int = 0
    output_tokens: int = 0
    success: bool = True
    error: str | None = None
    skipped_budget: bool = False


@dataclass
class ExtractionResult:
    """Combined result across all pillar groups."""
    group_results: list[ExtractionGroupResult] = field(default_factory=list)
    all_pillars: dict = field(default_factory=dict)   # pillar_name -> pillar data
    completed_groups: list[str] = field(default_factory=list)
    skipped_groups: list[str] = field(default_factory=list)
    failed_groups: list[str] = field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    budget_exhausted_mid_paper: bool = False


def _get_sections_for_group(
    group_pillars: list[str],
    routing_map: dict[str, list[int]],
    sections_by_index: dict[int, Section],
) -> list[tuple[int, str, str, str]]:
    """
    Collect unique sections needed for a pillar group.
    Returns list of (index, heading, content, formula_quality) sorted by index.
    """
    needed_indices: set[int] = set()
    for pillar in group_pillars:
        for idx in routing_map.get(pillar, []):
            if idx in sections_by_index:
                needed_indices.add(idx)

    result = []
    for idx in sorted(needed_indices):
        section = sections_by_index[idx]
        result.append((idx, section.heading, section.content, section.formula_quality))
    return result

def _parse_extraction_response(content: str) -> dict:
    """Parse JSON from extraction call. Strips markdown fences."""
    text = content.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(
            line for line in lines
            if not line.startswith("```")
        ).strip()
    return json.loads(text)


def _empty_pillar(pillar: str) -> dict:
    """Return an empty pillar structure for pillars with no sections."""
    return {
        "present": False,
        "source_note": None,
        "rules": [],
        "missing_note": f"No relevant sections identified by router for {pillar}.",
    }


async def extract_pillar_group(
    group_name: str,
    group_pillars: list[str],
    routing_map: dict[str, list[int]],
    sections_by_index: dict[int, Section],
    budget_manager: BudgetManager,
    max_tokens: int = 6000,
    paper_id: str | None = None,
) -> ExtractionGroupResult:
    """
    Extract rules for one pillar group.

    Args:
        group_name: Name of the pillar group.
        group_pillars: List of pillar names in this group.
        routing_map: Router output — pillar to section indices.
        sections_by_index: All sections keyed by index.
        budget_manager: LLM budget manager.
        max_tokens: Output token budget for this call.
        paper_id: For per-paper budget tracking.

    Returns:
        ExtractionGroupResult with extracted pillar data.
    """
    result = ExtractionGroupResult(
        group_name=group_name,
        pillars=group_pillars,
    )

    section_contents = _get_sections_for_group(
        group_pillars, routing_map, sections_by_index
    )

    # If router found no sections for any pillar in this group
    if not section_contents:
        logger.info(
            "Group %r: no sections found — marking all pillars absent",
            group_name,
        )
        result.extracted = {p: _empty_pillar(p) for p in group_pillars}
        return result

    prompt = build_extraction_prompt(
        group_name=group_name,
        pillars_in_group=group_pillars,
        section_contents=section_contents,
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
                task=f"strategy_extraction_{group_name}",
                paper_id=paper_id,
            )
            result.input_tokens = response.input_tokens
            result.output_tokens = response.output_tokens

            parsed = _parse_extraction_response(response.content)

            # Ensure all pillars in group are present in output
            for pillar in group_pillars:
                if pillar not in parsed:
                    logger.warning(
                        "Group %r: pillar %r missing from LLM output — marking absent",
                        group_name, pillar,
                    )
                    parsed[pillar] = _empty_pillar(pillar)

            result.extracted = {p: parsed[p] for p in group_pillars}
            result.success = True

            logger.info(
                "Group %r: extracted %d pillars, %d input / %d output tokens",
                group_name, len(result.extracted),
                result.input_tokens, result.output_tokens,
            )
            return result

        except json.JSONDecodeError as e:
            if attempt == 1:
                logger.warning(
                    "Group %r: malformed JSON on attempt %d — retrying: %s",
                    group_name, attempt, e,
                )
                continue
            logger.error("Group %r: malformed JSON after retry", group_name)
            result.success = False
            result.error = f"Malformed JSON after retry: {e}"
            result.extracted = {p: _empty_pillar(p) for p in group_pillars}
            return result

        except BudgetExhaustedError as e:
            logger.warning(
                "Group %r: budget exhausted — skipping remaining groups",
                group_name,
            )
            result.success = False
            result.skipped_budget = True
            result.error = str(e)
            result.extracted = {p: _empty_pillar(p) for p in group_pillars}
            raise   # re-raise so pipeline can stop remaining groups

        except LLMProviderError as e:
            logger.error("Group %r: LLM provider error: %s", group_name, e)
            result.success = False
            result.error = str(e)
            result.extracted = {p: _empty_pillar(p) for p in group_pillars}
            return result

        except Exception as e:
            logger.error("Group %r: unexpected error: %s", group_name, e)
            result.success = False
            result.error = str(e)
            result.extracted = {p: _empty_pillar(p) for p in group_pillars}
            return result

    return result


async def extract_all_groups(
    routing_map: dict[str, list[int]],
    not_found_pillars: list[str],
    sections_by_index: dict[int, Section],
    budget_manager: BudgetManager,
    pillar_groups: dict[str, list[str]] | None = None,
    max_tokens_per_group: int = 6000,
    paper_id: str | None = None,
) -> ExtractionResult:
    """
    Run Call 2 for all pillar groups in priority order.

    Stops on budget exhaustion — saves partial result as draft.
    Records which groups completed and which were skipped.

    Args:
        routing_map: Router output.
        not_found_pillars: Pillars with no sections from router.
        sections_by_index: All sections keyed by index.
        budget_manager: LLM budget manager.
        pillar_groups: Override default pillar groupings.
        max_tokens_per_group: Per-group output token budget.
        paper_id: For per-paper budget tracking.

    Returns:
        ExtractionResult with all pillar data.
    """
    groups = pillar_groups or DEFAULT_PILLAR_GROUPS
    result = ExtractionResult()

    # Pre-fill not_found pillars as absent
    for pillar in not_found_pillars:
        result.all_pillars[pillar] = _empty_pillar(pillar)

    for group_name, group_pillars in groups.items():
        # Skip pillars already handled (e.g. all were in not_found)
        pillars_to_extract = [
            p for p in group_pillars
            if p not in result.all_pillars
        ]
        if not pillars_to_extract:
            logger.debug("Group %r: all pillars already handled — skipping", group_name)
            result.completed_groups.append(group_name)
            continue

        try:
            group_result = await extract_pillar_group(
                group_name=group_name,
                group_pillars=pillars_to_extract,
                routing_map=routing_map,
                sections_by_index=sections_by_index,
                budget_manager=budget_manager,
                max_tokens=max_tokens_per_group,
                paper_id=paper_id,
            )
            result.group_results.append(group_result)
            result.all_pillars.update(group_result.extracted)
            result.total_input_tokens += group_result.input_tokens
            result.total_output_tokens += group_result.output_tokens

            if group_result.success:
                result.completed_groups.append(group_name)
            else:
                result.failed_groups.append(group_name)

        except BudgetExhaustedError:
            # Mark remaining groups as skipped
            result.budget_exhausted_mid_paper = True
            result.skipped_groups.append(group_name)
            remaining = list(groups.keys())
            remaining = remaining[list(groups.keys()).index(group_name) + 1:]
            result.skipped_groups.extend(remaining)

            # Fill skipped pillars as absent
            for remaining_group in remaining:
                for pillar in groups[remaining_group]:
                    if pillar not in result.all_pillars:
                        result.all_pillars[pillar] = _empty_pillar(pillar)

            logger.warning(
                "Budget exhausted mid-paper after group %r. "
                "Completed: %s. Skipped: %s.",
                group_name,
                result.completed_groups,
                result.skipped_groups,
            )
            break

    return result
