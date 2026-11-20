# src/quantforge/llm/prompts/strategy/extraction.py

"""
Call 2 — Pillar Extractor prompt.

Input:  pillar group name + relevant section content
Output: structured rule objects per pillar
"""

from __future__ import annotations

SYSTEM_PROMPT = """You are a systematic trading research analyst extracting strategy rules from academic papers.

Rules:
1. Extract only what the paper explicitly states or what can be directly inferred.
2. Mark explicitly stated rules as "explicit". Mark interpreted rules as "inferred".
3. For inferred rules, you MUST include an "evidence" field explaining your interpretation.
4. NEVER add rules the paper does not support. Do not fill gaps with reasonable defaults.
5. NEVER label a QuantForge assumption as "explicit" — that is a provenance violation.
6. Omit optional rule fields entirely when the paper provides nothing for them. Do not write null or placeholder values.
7. Copy formulas exactly. Do not simplify or restate them.
8. You must respond with a valid JSON object only. No preamble, no markdown fences.

FORMULA QUALITY RULES:
9. Each section is tagged with a formula_quality signal: clean, degraded, or missing.
   - clean: formulas appear intact — copy them exactly, mark rule as "explicit".
   - degraded: formulas show signs of garbling (broken symbols, LaTeX remnants, missing subscripts).
     Mark the rule as "inferred", describe what was unclear in the "evidence" field.
     Never reconstruct a formula beyond what the text clearly supports.
   - missing: no formula content detected — omit the "formula" field entirely.
     If a formula region is blank or absent, add a note in "notes". Do not reconstruct from surrounding prose alone.
"""

PILLAR_DEFINITIONS = {
    "market_regime": """Capture:
- How the paper classifies the market (index level, MA, breadth, volatility, macro)
- What data and timeframe it uses for classification
- What regimes it names and how it defines each
- What the strategy does differently per regime
Do NOT substitute your own bull/bear definition if the paper uses something different.""",

    "market_eligibility": """Capture:
- Which regimes permit new entries
- Maximum gross or net exposure
- Maximum simultaneous open positions
- Market-wide volatility or risk thresholds that gate trading
This is the ACTION taken because of a regime, not the regime classification itself.""",

    "asset_selection": """Capture:
- Asset class and market
- Liquidity, volume, or price minimums
- Market-cap or sector filters
- Momentum or relative-strength ranking criteria
- Rebalancing frequency for the universe""",

    "entry_signal": """Capture for each rule:
- The precise condition or formula
- Data inputs and timeframe
- Confirmation required before acting
- Whether entry is at close, intraday, or another time
- Whether the rule is mechanical or discretionary
If the paper has multiple confirmation layers, capture each separately.""",

    "position_sizing": """Capture:
- Initial sizing method (fixed, percentage, volatility-based, risk-based)
- Risk per trade if stated
- Maximum position size or cap
- Adding to winners, scaling out, partial profit-taking
- Trailing stop adjustments and re-entry rules""",

    "risk_management": """Capture:
- Stop-loss rules (fixed, ATR-based, percentage)
- Maximum drawdown limits
- Risk per trade or per day
- Correlation or concentration limits
- Hedge rules""",

    "exit": """Capture:
- Rebalancing schedule
- Profit target rules
- Stop-loss exit triggers
- Time-based exits
- Regime-based exits
If only rebalancing is described with no stop rules, record that explicitly.""",

    "portfolio": """Capture:
- Number of positions held
- Long/short allocation
- Sector or factor exposure limits
- Rebalancing frequency and method
- Cash allocation rules""",

    "backtesting": """Capture:
- Data period and source
- In-sample vs out-of-sample split
- Transaction cost assumptions
- Benchmark used
- Key performance metrics reported (Sharpe, CAGR, max drawdown)""",
}

WORKED_EXAMPLE = """
WORKED EXAMPLE (from a simplified 12-1 month momentum paper):

Pillar: asset_selection
{
  "present": true,
  "source_note": "Section 2, p.4",
  "rules": [
    {
      "rule_id": "universe_1",
      "description": "Trade NYSE and NASDAQ common stocks with prior-month price above $5.",
      "provenance": "explicit",
      "source_ref": "Section 2, p.4"
    },
    {
      "rule_id": "universe_2",
      "description": "Exclude stocks in the bottom NYSE size decile.",
      "provenance": "explicit",
      "source_ref": "Section 2, p.4"
    }
  ]
}

Pillar: entry_signal
{
  "present": true,
  "source_note": "Section 3, p.6",
  "rules": [
    {
      "rule_id": "entry_1",
      "description": "Buy the top decile of stocks ranked by 12-month return, skipping the most recent month.",
      "formula": "R(t-12, t-1) = (P_{t-1} / P_{t-12}) - 1",
      "timeframe": "monthly",
      "data_inputs": "monthly closing price",
      "provenance": "explicit",
      "source_ref": "Section 3, p.6"
    }
  ]
}

Pillar: exit (partial — no stop-loss specified)
{
  "present": "partial",
  "source_note": "Section 3, p.7",
  "rules": [
    {
      "rule_id": "exit_1",
      "description": "Rebalance monthly. Exit positions that fall out of the top decile at rebalance.",
      "provenance": "explicit",
      "source_ref": "Section 3, p.7"
    }
  ],
  "missing_note": "No stop-loss or drawdown exit rule is specified."
}
"""


def build_extraction_prompt(
    group_name: str,
    pillars_in_group: list[str],
    section_contents: list[tuple[int, str, str, str]],  # (index, heading, content, formula_quality)
) -> str:
    """
    Build the extraction prompt for one pillar group.

    Args:
        group_name: Name of the pillar group
        pillars_in_group: List of pillar names to extract
        section_contents: List of (index, heading, content, formula_quality)

    Returns:
        User prompt string.
    """
    pillar_defs = "\n\n".join(
        f"PILLAR: {p}\n{PILLAR_DEFINITIONS.get(p, '')}"
        for p in pillars_in_group
    )

    sections_text = "\n\n".join(
        f"[Section {idx}] {heading} [formula_quality: {fq}]\n{content}"
        for idx, heading, content, fq in section_contents
    )

    return f"""Extract strategy rules from the following paper sections.

EXTRACTION GROUP: {group_name}
PILLARS TO EXTRACT: {', '.join(pillars_in_group)}

PILLAR DEFINITIONS:
{pillar_defs}

{WORKED_EXAMPLE}

PAPER SECTIONS:
{sections_text}

REQUIRED OUTPUT FORMAT (JSON only, no other text):
{{
  {', '.join(f'"{p}": {{"present": true/false/"partial", "source_note": "...", "rules": [...], "missing_note": "..."}}' for p in pillars_in_group)}
}}

RULES FOR EACH RULE OBJECT:
- rule_id: short unique identifier (e.g. "entry_1", "exit_2")
- description: what the rule says, in precise language
- provenance: "explicit" or "inferred" (never "quantforge_added" in a base spec)
- source_ref: section and page reference
- formula: include only if the paper gives one AND formula_quality is "clean" (omit field entirely if degraded or missing)
- timeframe: omit if not stated
- data_inputs: omit if not stated
- evidence: REQUIRED when provenance is "inferred" — explain your interpretation
- notes: use for formula quality issues (e.g. "Formula appears garbled in source PDF")

If a pillar has no relevant content in these sections, set present=false and omit rules."""
