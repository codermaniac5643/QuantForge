# src/quantforge/llm/prompts/strategy/validation.py

"""
Call 3 — Validation and Assessment prompt.

Input:  all extracted rule objects + missing component list
Output: completeness classification, methodology assessment, flagged rules,
        cross-pillar leakage findings
"""

from __future__ import annotations

SYSTEM_PROMPT = """You are a systematic trading research analyst validating an extracted strategy specification.

Your task is to:
1. Classify the strategy's completeness.
2. Assess its methodology.
3. Flag any rules that violate extraction standards.
4. Identify cross-pillar leakage — rules placed in the wrong pillar.

You must respond with a valid JSON object only. No preamble, no markdown fences.
"""

COMPLETENESS_CLASSES = """
COMPLETENESS CLASSIFICATIONS:
- complete_strategy: Addresses entry, exit, sizing, and risk in sufficient detail to implement.
- entry_focused: Primarily describes when to buy/sell. Other components absent or vague.
- exit_focused: Primarily addresses stops, profit targets, or trade management.
- sizing_focused: Primarily addresses position sizing or capital allocation.
- risk_focused: Primarily addresses risk management, drawdown control, or exposure limits.
- regime_focused: Primarily addresses market regime classification.
- portfolio_focused: Primarily addresses portfolio construction or allocation.
- research_only: Describes findings but specifies no implementable rules.
"""

LEAKAGE_PAIRS = """
COMMON CROSS-PILLAR LEAKAGE PATTERNS TO CHECK:
- Exit rules (stops, profit targets, time exits) placed inside entry_signal
- Sizing rules (position size, risk per trade) placed inside risk_management
- Risk rules (drawdown limits, concentration limits) placed inside position_sizing
- Market regime classification placed inside market_eligibility (regime is what it IS, eligibility is what you DO because of it)
- Asset selection filters placed inside entry_signal
- Portfolio-level rules (total exposure, number of positions) placed inside position_sizing
"""


def build_validation_prompt(
    extracted_pillars: dict,
    missing_components: list[dict],
    not_found_pillars: list[str],
) -> str:
    """
    Build the validation prompt.

    Args:
        extracted_pillars: Dict of pillar_name -> pillar data from Call 2.
        missing_components: List of missing component records.
        not_found_pillars: Pillars the router couldn't find sections for.

    Returns:
        User prompt string.
    """
    import json

    pillars_json = json.dumps(extracted_pillars, indent=2)
    missing_json = json.dumps(missing_components, indent=2)

    return f"""Validate this extracted strategy specification.

EXTRACTED PILLARS:
{pillars_json}

MISSING COMPONENTS:
{missing_json}

PILLARS NOT FOUND BY ROUTER: {not_found_pillars}

{COMPLETENESS_CLASSES}

{LEAKAGE_PAIRS}

VALIDATION TASKS:
1. Assign a completeness_class from the list above.
2. Write a methodology_assessment covering implementability and backtest suitability.
3. Flag any explicit rules that lack a clear source reference (source_ref missing or vague).
4. Check for cross-pillar leakage — rules that belong in a different pillar than where they were placed.
5. Flag any inferred rules that are missing their evidence field.

REQUIRED OUTPUT FORMAT (JSON only, no other text):
{{
  "completeness_class": "<one of the classifications above>",
  "methodology_assessment": {{
    "implementability": "directly_implementable | requires_additions | research_only",
    "suitable_for_backtest": true | false,
    "primary_strength": "<one sentence>",
    "primary_weakness": "<one sentence>",
    "key_missing_components": ["pillar_name", ...],
    "reason": "<two to three sentences>"
  }},
  "flagged_rules": [
    {{
      "pillar": "<pillar_name>",
      "rule_id": "<rule_id>",
      "issue": "<description of the problem>"
    }}
  ],
  "leakage_findings": [
    {{
      "found_in_pillar": "<where it was placed>",
      "rule_id": "<rule_id>",
      "should_be_in_pillar": "<correct pillar>",
      "reason": "<why it belongs there>"
    }}
  ]
}}"""