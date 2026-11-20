
# src/quantforge/strategy/extraction/router.py

"""
Call 1 — Section Router.

Sends section titles and the abstract to the LLM.
Returns a mapping of pillar names to relevant section indices,
plus a list of pillars for which no relevant section was found.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from quantforge.llm.budget import BudgetManager
from quantforge.llm.provider import LLMRequest, LLMProviderError
from quantforge.llm.prompts.strategy.routing import (
    SYSTEM_PROMPT,
    PILLARS,
    build_routing_prompt,
)
from quantforge.strategy.extraction.chunker import Section

logger = logging.getLogger(__name__)


@dataclass
class RoutingResult:
    """Output of the section router call."""
    routing_map: dict[str, list[int]] = field(default_factory=dict)
    not_found: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    success: bool = True
    error: str | None = None


def _parse_routing_response(content: str) -> dict:
    """
    Parse JSON from the routing call response.
    Strips markdown fences if present.
    """
    text = content.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(
            line for line in lines
            if not line.startswith("```")
        ).strip()
    return json.loads(text)


async def route_sections(
    sections: list[Section],
    abstract: str,
    budget_manager: BudgetManager,
    max_tokens: int = 1000,
    paper_id: str | None = None,
) -> RoutingResult:
    """
    Call 1 — route paper sections to strategy pillars.

    Args:
        sections: Filtered, quality-checked sections.
        abstract: Paper abstract text.
        budget_manager: LLM budget manager.
        max_tokens: Output token budget for this call.
        paper_id: For budget manager per-paper tracking.

    Returns:
        RoutingResult with routing_map and not_found list.
    """
    result = RoutingResult()

    if not sections:
        logger.warning("Router received no sections — all pillars go to not_found")
        result.not_found = list(PILLARS)
        result.success = False
        result.error = "No sections available for routing"
        return result

    # Build section title list for the prompt
    section_titles = [(s.index, s.heading) for s in sections]

    prompt = build_routing_prompt(
        abstract=abstract,
        section_titles=section_titles,
    )

    request = LLMRequest(
        system_prompt=SYSTEM_PROMPT,
        prompt=prompt,
        max_tokens=max_tokens,
        temperature=0.0,   # routing is deterministic
    )

    # Attempt with one retry on malformed JSON
    for attempt in range(1, 3):
        try:
            response = await budget_manager.call(
                request=request,
                task="strategy_routing",
                paper_id=paper_id,
            )
            result.input_tokens = response.input_tokens
            result.output_tokens = response.output_tokens

            parsed = _parse_routing_response(response.content)
            routing_map = parsed.get("routing_map", {})
            not_found = parsed.get("not_found", [])

            # Validate — ensure all pillars are accounted for
            for pillar in PILLARS:
                if pillar not in routing_map and pillar not in not_found:
                    not_found.append(pillar)

            # Filter out indices that don't exist in our section list
            valid_indices = {s.index for s in sections}
            cleaned_map: dict[str, list[int]] = {}
            for pillar, indices in routing_map.items():
                valid = [i for i in indices if i in valid_indices]
                if valid:
                    cleaned_map[pillar] = valid
                else:
                    not_found.append(pillar)

            result.routing_map = cleaned_map
            result.not_found = list(set(not_found))
            result.success = True

            logger.info(
                "Router: %d pillars mapped, %d not found",
                len(cleaned_map), len(result.not_found),
            )
            return result

        except json.JSONDecodeError as e:
            if attempt == 1:
                logger.warning(
                    "Router: malformed JSON on attempt %d — retrying: %s",
                    attempt, e,
                )
                continue
            logger.error("Router: malformed JSON after retry — marking failed")
            result.success = False
            result.error = f"Malformed JSON after retry: {e}"
            result.not_found = list(PILLARS)
            return result

        except LLMProviderError as e:
            logger.error("Router: LLM provider error: %s", e)
            result.success = False
            result.error = str(e)
            result.not_found = list(PILLARS)
            return result

        except Exception as e:
            logger.error("Router: unexpected error: %s", e)
            result.success = False
            result.error = str(e)
            result.not_found = list(PILLARS)
            return result

    return result