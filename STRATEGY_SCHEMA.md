# QuantForge — Strategy Schema

**Document:** `STRATEGY_SCHEMA.md`
**Phase:** Strategy Engineering
**Purpose:** Defines the structure of a Strategy Specification — the machine-readable representation of a paper's trading methodology extracted during Strategy Engineering.

---

## 1. Purpose and Scope

A Strategy Specification captures what a paper actually says about how to trade. It is not a backtest configuration, a live trading plan, or a code implementation. It is the structured record that sits between a processed research paper and eventual implementation.

Every rule must be traceable to its source. Rules the paper stated explicitly, rules that were interpreted, and rules added by QuantForge must never be mixed without labels.

A specification covers only what the paper addresses. Missing components are recorded as missing — they are not invented.

---

## 2. Provenance Labels

Every rule carries one of three labels. Never omit them.

| Label | Meaning |
|---|---|
| `explicit` | Directly and clearly stated by the paper. |
| `inferred` | Interpreted from ambiguous or incomplete source material. Must include supporting evidence. |
| `quantforge_added` | Added by QuantForge. Not from the paper. Belongs in a variant only, never the base spec. |

---

## 3. Completeness Classification

Before extraction begins, classify what the paper actually covers.

| Classification | Description |
|---|---|
| `complete_strategy` | Addresses entry, exit, sizing, and risk in sufficient detail to implement. |
| `entry_focused` | Primarily describes when to buy or sell. Other components are absent or vague. |
| `exit_focused` | Primarily addresses stops, profit targets, or trade management. |
| `sizing_focused` | Primarily addresses position sizing or capital allocation. |
| `risk_focused` | Primarily addresses risk management, drawdown control, or exposure limits. |
| `regime_focused` | Primarily addresses market regime classification or regime-conditional behavior. |
| `portfolio_focused` | Primarily addresses portfolio construction, rebalancing, or allocation. |
| `research_only` | Describes findings or evidence but does not specify implementable rules. |

Also record component coverage — `present`, `partial`, or `absent` — for each of the nine pillars before you start extracting.

---

## 4. Top-Level Specification Structure

```
strategy_id:          Internal QuantForge identifier. Generated on creation.
paper_id:             Reference to the source paper record in the database.
version:              Integer. Incremented on every material change. Starts at 1.
variant:              "base" for the paper-faithful extraction. Named string for enhanced variants.
status:               draft | under_review | approved | rejected | superseded
created_at:           ISO 8601 timestamp.
updated_at:           ISO 8601 timestamp.
extracted_by:         "llm_assisted" | "manual" | "hybrid"
reviewed_by:          Identifier of the human reviewer, if reviewed.
completeness_class:   One value from Section 3.
component_coverage:   Object — present/partial/absent for each pillar.
notes:                Free text. Limitations, caveats, extraction difficulties.
pillars:              Object containing the nine extraction pillars (Section 5).
```

---

## 5. The Nine Extraction Pillars

Each pillar follows the same structure:

```
present:      true | false | partial
source_note:  Where in the paper this was found. Section or page reference.
rules:        List of rule objects (see Section 6).
missing_note: If absent or partial — what is missing and why it matters.
```

---

### Pillar 1 — Market Regime

What market conditions does the paper define, and how does it act on them?

Capture:
- How the paper classifies the market (index level, moving average, breadth, volatility, macro, etc.)
- What data and timeframe it uses
- What regimes it names and how it defines each
- What the strategy does differently per regime (stops trading, reduces exposure, hedges, exits)

**Do not substitute your own bull/bear definition if the paper uses something different.**
If the paper has no market regime definition, record it as absent.

---

### Pillar 2 — Market Eligibility and Exposure Conditions

Under what conditions is the strategy allowed to open positions at all?

Capture:
- Which regimes permit new entries
- Maximum gross or net exposure
- Maximum simultaneous open positions
- Market-wide volatility or risk thresholds that gate trading

This is the action taken because of a regime, not the regime classification itself.

---

### Pillar 3 — Asset or Instrument Selection

What does the strategy trade and how does it select instruments?

Capture:
- Asset class and market
- Liquidity, volume, or price minimums
- Market-cap or sector filters
- Momentum or relative-strength ranking criteria
- Rebalancing frequency

---

### Pillar 4 — Entry Signal and Confirmation

What are the exact conditions that trigger a trade?

Capture for each rule:
- The precise condition or formula
- Data inputs and timeframe
- Confirmation required before acting
- Whether entry is evaluated at close, intraday, or another time
- Whether the rule is mechanical or discretionary

If the paper has multiple confirmation layers, capture each separately.

---

### Pillar 5 — Position Sizing and Management

How large is each position, and how is it managed after entry?

Capture:
- Initial sizing method (fixed, percentage, volatility-based, risk-based)
- Risk per trade if stated
- Maximum position size or cap
- Adding to winners, scaling out, partial profit-taking
- Trailing stop adjustments
- Re-entry rules
- Maximum holding period

**If the paper does not specify sizing, record it as absent. Do not invent one.**

---

### Pillar 6 — Risk Management

How does the strategy control downside?

Capture:
- Initial stop-loss or invalidation rule
- Risk per trade as percentage of capital
- Maximum portfolio drawdown limit
- Concentration limits per position or sector
- Risk actions triggered by regime changes

---

### Pillar 7 — Exit and Trade Management

What conditions close a trade?

Capture each exit type separately:
- Stop-loss or hard invalidation
- Profit target
- Trailing stop
- Signal reversal or entry-condition failure
- Time-based or maximum holding exit
- Regime deterioration exit
- Discretionary exit criteria

Exits must be recorded separately from entries.

---

### Pillar 8 — Portfolio and Capital Management

How is capital allocated across positions?

Capture:
- Maximum number of open positions
- Position weighting method (equal, risk-weighted, momentum-weighted)
- Concentration limit per position
- Sector or asset-class allocation limits
- Rebalancing frequency and trigger
- Portfolio-level drawdown constraint

---

### Pillar 9 — Backtesting Methodology

How did the paper test its strategy? This describes the paper's own test — not QuantForge's future backtest.

Capture:
- Data source and date range
- Universe construction
- Transaction cost and slippage assumptions
- Execution assumptions (market, close, next-open)
- Survivorship and look-ahead bias controls
- In-sample and out-of-sample split
- Validation method used
- Reported performance metrics

Also record your honest assessment: were the assumptions realistic? Were there obvious methodological concerns?

---

## 6. Rule Object Structure

Every rule is recorded as a rule object. Keep it simple — only add fields that the paper actually provides.

**Required fields (always present):**
```
rule_id:      Short identifier. e.g. "entry_1", "stop_1"
description:  Plain-language description of the rule.
provenance:   explicit | inferred | quantforge_added
source_ref:   Section or page from the paper. e.g. "Section 3.2" or "p.14"
```

**Optional fields (add only when the paper provides them):**
```
formula:      Exact formula as written in the paper. Copy it — do not restate it.
timeframe:    Data timeframe the rule applies to. e.g. "daily", "weekly"
data_inputs:  Data fields required. e.g. "close price, 50-day MA, volume"
evidence:     Required when provenance is "inferred". The text or reasoning behind the interpretation.
notes:        Ambiguities, limitations, or implementation considerations.
```

**Rules for filling these in:**
- If provenance is `inferred`, `evidence` is required.
- If the paper gives a formula, copy it exactly into `formula`. Do not simplify.
- If a field has no information from the paper, leave it out entirely. Do not write `null` or `"not specified"` — omit the field.

---

## 7. Missing Component Record

When a pillar is absent or incomplete, record it here rather than leaving it silent.

```
pillar:             Name of the pillar.
status:             absent | partial
impact:             critical | moderate | minor
description:        What is missing.
implication:        What cannot be done without this component.
recommended_action: ignore | requires_human_decision | blocks_implementation
```

A `critical` missing component — typically sizing, stops, or exits — means the strategy cannot be implemented faithfully from the base spec alone. Any rule added to fill the gap is `quantforge_added` and belongs in a variant.

---

## 8. Methodology Assessment

A concise judgment of the paper's practical value. Keep it honest and brief.

```
implementability:       directly_implementable | requires_additions | research_only
suitable_for_backtest:  true | false
primary_strength:       One sentence.
primary_weakness:       One sentence.
key_missing_components: List of the most important gaps.
reason:                 Two to three sentences explaining the implementability and backtest suitability.
```

`directly_implementable` means all nine pillars are sufficiently covered by explicit or inferred rules — no `quantforge_added` rules are needed to run a backtest.

`requires_additions` means one or more critical pillars are missing and a named variant with `quantforge_added` rules would be needed before backtesting.

`research_only` means the paper contains no implementable rules.

---

## 9. Extraction Metadata

```
extraction_method:  llm_assisted | manual | hybrid
extraction_date:    ISO 8601 timestamp.
extractor_notes:    Difficulties, ambiguities, or decisions made during extraction.
review_status:      pending | reviewed | approved
reviewer_notes:     Human reviewer's comments, if reviewed.
```

LLM-assisted extraction must be reviewed by a human before status moves to `approved`.

---

## 10. Variants

The `base` variant contains only what the paper supports — `explicit` and `inferred` rules only.

A variant adds `quantforge_added` rules to make an incomplete base spec implementable or to test enhancements.

```
variant_id:     Short identifier. e.g. "with_market_filter"
based_on:       strategy_id and version of the base spec.
description:    What this variant adds and why.
added_rules:    List of rule objects with provenance "quantforge_added".
modified_rules: List of rule_ids from the base that are changed, and the new values.
rationale:      Why each addition or modification was made.
```

A variant must be backtested and reported separately. Its results must never be attributed to the original paper.

---

## 11. Review Workflow

A specification moves through these statuses:

| Status | Meaning | Who sets it |
|---|---|---|
| `draft` | Extraction in progress or just completed. | Extractor |
| `under_review` | Awaiting human review. | Extractor |
| `approved` | Reviewed and confirmed accurate. | Reviewer |
| `rejected` | Extraction found to be inaccurate or insufficient. Needs redo. | Reviewer |
| `superseded` | A newer version of this spec exists. | System |

**To move from `under_review` to `approved`, the reviewer must confirm:**
1. Every rule has a valid provenance label.
2. Every `inferred` rule has evidence recorded.
3. No `quantforge_added` rules appear in the base spec.
4. Missing pillars are recorded, not silently skipped.
5. The completeness classification matches the actual coverage.
6. The methodology assessment is consistent with the extracted content.

If any of these fail, status goes to `rejected` with reviewer notes explaining what needs to be corrected.

---

## 12. Filesystem Location

```
research_library/
└── <paper_id>/
    ├── paper.pdf
    ├── text.txt
    ├── sections.json
    ├── metadata.json
    ├── acquisition.json
    └── strategy/
        ├── spec_v1.json
        ├── spec_v2.json
        └── variants/
            └── with_market_filter_v1.json
```

The database records existence, version, status, and file path. The file holds the full content.

---

## 13. Database Record

```
strategy_specifications table:
  strategy_id        UUID, primary key
  paper_id           Foreign key to research_papers
  version            Integer
  variant            String, default "base"
  status             draft | under_review | approved | rejected | superseded
  completeness_class String
  file_path          Path to the JSON file on disk
  extracted_by       String
  reviewed_by        String, nullable
  created_at         Timestamp
  updated_at         Timestamp
```

One paper may have multiple specifications (multiple versions and variants). The latest approved base specification is the canonical one.

---

## 14. Worked Example

This example is based on a simplified momentum paper to illustrate how the schema is applied in practice.

**Paper:** "12-1 Month Momentum in US Equities" (hypothetical)
**Completeness class:** `entry_focused`
**Component coverage:**

| Pillar | Coverage |
|---|---|
| Market Regime | absent |
| Market Eligibility | absent |
| Asset Selection | present |
| Entry Signal | present |
| Position Sizing | absent |
| Risk Management | absent |
| Exit | partial |
| Portfolio Management | partial |
| Backtesting Methodology | present |

---

**Pillar 3 — Asset Selection**

```json
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
```

---

**Pillar 4 — Entry Signal**

```json
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
```

---

**Pillar 7 — Exit (partial)**

```json
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
  "missing_note": "No stop-loss or drawdown exit rule is specified. The paper only describes monthly rebalancing as the exit mechanism."
}
```

---

**Missing Component Records**

```json
[
  {
    "pillar": "position_sizing",
    "status": "absent",
    "impact": "critical",
    "description": "Paper does not specify how much capital to allocate per position.",
    "implication": "Cannot run a backtest without a sizing assumption.",
    "recommended_action": "blocks_implementation"
  },
  {
    "pillar": "risk_management",
    "status": "absent",
    "impact": "critical",
    "description": "No stop-loss, drawdown limit, or risk-per-trade rule is specified.",
    "implication": "No downside protection in the base spec.",
    "recommended_action": "blocks_implementation"
  },
  {
    "pillar": "market_regime",
    "status": "absent",
    "impact": "moderate",
    "description": "Paper makes no distinction between bull and bear market conditions.",
    "implication": "Strategy trades in all conditions including severe drawdowns.",
    "recommended_action": "requires_human_decision"
  }
]
```

---

**Methodology Assessment**

```json
{
  "implementability": "requires_additions",
  "suitable_for_backtest": false,
  "primary_strength": "Entry signal is precisely defined with a clear formula and data source.",
  "primary_weakness": "No position sizing or risk management rules — the two most critical missing components.",
  "key_missing_components": ["position_sizing", "risk_management", "market_regime"],
  "reason": "The entry signal can be implemented directly, but no base-spec backtest is possible without inventing a sizing method and stop rules. These additions must be tracked in a named variant. The paper is a valid research input but is not a complete strategy."
}
```

---

**What would happen next:** A variant named `"equal_weight_with_stop"` would be created, adding `quantforge_added` rules for equal-weight sizing and a drawdown stop. That variant gets its own backtest and its results are never attributed to the original paper.

---

## 15. Practical Notes for Extraction

- Read the full paper before starting. Do not extract from the abstract alone.
- Copy formulas exactly. Do not simplify or restate them.
- When a rule is ambiguous, write what you interpreted and why. Mark it `inferred`.
- When unsure whether a rule is explicit or inferred, prefer `inferred`.
- Do not fill missing pillars with reasonable defaults labeled `explicit`. That is a provenance violation.
- Short or narrow papers will cover only a few pillars. That is expected — record the rest as absent.
- Omit optional rule fields entirely when the paper provides nothing for them. Do not write placeholder values.
- LLM-assisted extraction must be reviewed by a human before status moves to `approved`.

---

## 16. What This Schema Is Not

- Not a backtest configuration. Rules here describe what the paper says, not what QuantForge will test.
- Not executable code. Implementation is a later phase.
- Not a performance claim. Reported results in Pillar 9 are the paper's own test — not QuantForge validation.
- Not a complete strategy by default. Most papers will have missing components. Record them honestly.
