# src/quantforge/llm/prompts/strategy/routing.py

"""
Call 1 — Section Router prompt.

Input:  section titles + abstract
Output: mapping of pillar names to relevant section indices + not_found list
"""

from __future__ import annotations

SYSTEM_PROMPT = """You are a systematic trading research analyst.
Your task is to map strategy pillars to the most relevant sections of a research paper.

You must respond with a valid JSON object only. No preamble, no explanation, no markdown fences.
"""

PILLARS = [
    "market_regime",
    "market_eligibility",
    "asset_selection",
    "entry_signal",
    "position_sizing",
    "risk_management",
    "exit",
    "portfolio",
    "backtesting",
]

PILLAR_DESCRIPTIONS = {
    "market_regime": "Market condition classification (bull/bear, volatility, breadth, moving averages)",
    "market_eligibility": "Conditions that allow or block new position entries (regime gates, exposure limits)",
    "asset_selection": "What instruments to trade and how to select/filter/rank them",
    "entry_signal": "Exact conditions and formulas that trigger a trade entry",
    "position_sizing": "How large each position is and how it is managed after entry",
    "risk_management": "Stop-losses, drawdown limits, risk-per-trade rules",
    "exit": "When and how to exit positions (rebalancing, stops, profit targets)",
    "portfolio": "Portfolio construction, allocation, rebalancing across positions",
    "backtesting": "Test methodology, data period, performance metrics reported",
}


def build_routing_prompt(
    abstract: str,
    section_titles: list[tuple[int, str]],  # [(index, heading), ...]
) -> str:
    """
    Build the routing prompt.

    Args:
        abstract: Paper abstract text.
        section_titles: List of (section_index, heading) tuples.

    Returns:
        User prompt string.
    """
    pillar_list = "\n".join(
        f"  - {name}: {desc}"
        for name, desc in PILLAR_DESCRIPTIONS.items()
    )

    section_list = "\n".join(
        f"  [{i}] {title}" for i, title in section_titles
    )

    return f"""You are routing a research paper's sections to strategy extraction pillars.

ABSTRACT:
{abstract[:1500]}

SECTIONS (index: title):
{section_list}

PILLARS TO MAP:
{pillar_list}

TASK:
For each pillar, list the section indices most likely to contain relevant content.
A section can appear under multiple pillars.
If no section seems relevant for a pillar, include it in "not_found".

REQUIRED OUTPUT FORMAT (JSON only, no other text):
{{
  "routing_map": {{
    "market_regime": [<indices>],
    "market_eligibility": [<indices>],
    "asset_selection": [<indices>],
    "entry_signal": [<indices>],
    "position_sizing": [<indices>],
    "risk_management": [<indices>],
    "exit": [<indices>],
    "portfolio": [<indices>],
    "backtesting": [<indices>]
  }},
  "not_found": ["pillar_name", ...]
}}"""