# QuantForge — Project Specification

**Document:** `PROJECT_SPEC.md`  
**Status:** Active — Strategy Engineering phase  
**Project name:** QuantForge  
**Tagline:** Quantitative Research & Strategy Engineering Platform

---

## 1. Project Intent

### 1.1 Vision

QuantForge is an AI-assisted quantitative research and strategy engineering platform. Its purpose is to turn academic research and market knowledge into **reproducible, testable, validated, and eventually deployable trading strategies**.

The platform is intended to function as a research laboratory—not merely a paper search engine or a code-generation assistant. It should help us discover relevant research, understand the methodology, identify the rules that make a strategy work, implement those rules faithfully, and evaluate them through rigorous quantitative testing.

### 1.2 What We Are Trying to Achieve

QuantForge should help us:

1. **Discover research systematically** — Find academic papers and research material related to trading strategies, market behavior, macroeconomics, business cycles, market regimes, market structure, behavioral finance, and portfolio theory.
2. **Identify research worth investigating** — Filter, deduplicate, evaluate, and rank research based on relevance, credibility, implementability, reproducibility, and research priority.
3. **Understand a paper's actual methodology** — Extract its hypotheses, data, market assumptions, entry and exit rules, risk controls, position-management rules, portfolio construction, and reported results.
4. **Translate research into structured strategy specifications** — Represent extracted methodology in a consistent, machine-readable format before implementation.
5. **Implement strategies faithfully** — Build a reproducible Python implementation of the original methodology, while keeping QuantForge modifications or enhancements separate.
6. **Backtest and validate strategies** — Evaluate performance using deterministic calculations, realistic trading assumptions, robustness tests, and appropriate risk and performance metrics.
7. **Build an auditable research record** — Preserve paper sources, extracted rules, implementation versions, configurations, test results, and decisions so every result can be traced to its origin.
8. **Progress toward paper trading and controlled live deployment** — Treat deployment as a later phase that depends on research quality, validation, operational controls, and explicit approval.

### 1.3 Guiding Principles

- **Research first:** Discover and understand the evidence before implementing a strategy.
- **Reproducibility:** A result should be repeatable from recorded data, code, configuration, and assumptions.
- **Methodology fidelity:** The original paper's rules must not be silently changed.
- **Explicit separation:** Original research, inferred rules, and QuantForge-added rules must be distinguishable.
- **Quantitative correctness:** Signals, position sizing, risk calculations, portfolio accounting, and backtests must be computed by deterministic code.
- **Selective AI usage:** Use an LLM where language understanding or reasoning is useful—not for calculations that should be deterministic.
- **Risk-aware research:** Entry signals alone are insufficient. Risk, exits, position management, and portfolio exposure are essential parts of strategy research.
- **Traceability:** Every strategy rule and result should be linked to its source, implementation, and version.
- **Modularity:** Research sources, evaluation methods, data providers, and strategy components should be replaceable or extendable.
- **Cost control:** LLM usage must be bounded, measured, and governed by a central budget.
- **Incremental development:** Implement and verify each phase before adding unnecessary complexity.

### 1.4 What QuantForge Is Not

QuantForge is not intended to be:

- A paper downloader without research analysis.
- A platform that blindly converts every paper into trading code.
- An LLM that independently calculates backtest results or controls unrestricted live trading.
- A system that mixes a paper's original rules with undocumented enhancements.
- A machine-learning strategy platform by default. Initial strategy research and implementation should prioritize rule-based and quantitative methods; ML is not a required component.
- An autonomous live-trading system in the initial phases.

---

## 2. Research Philosophy and Strategy Analysis Framework

QuantForge must evaluate a strategy as a **complete trading methodology**, not just an entry signal.

A strategy may contain several interacting layers. Research extraction should identify each layer, preserve the paper's own definitions, and record whether a rule is explicit, inferred, or added by QuantForge.

### 2.1 Core Research Pillars

Every relevant paper should be examined across the following dimensions, where applicable:

1. **General Market Analysis and Market Regime**
2. **Market Eligibility and Exposure Conditions**
3. **Stock-Level Price Action and Volume**
4. **Strategy Signals and Confirmations**
5. **Position Sizing and Position Management**
6. **Risk Management**
7. **Exit and Trade Management**
8. **Portfolio and Capital Management**
9. **Backtesting Methodology and Robustness**

A paper may focus on only one or two pillars. QuantForge must identify its scope and explicitly document any missing components.

### 2.2 General Market Analysis (GM) and Market Regime

QuantForge must extract how a paper defines and uses the broader market environment.

Relevant questions include:

- Does the paper distinguish bull, bear, sideways/choppy, high-volatility, low-volatility, or transition regimes?
- Does it define a market regime explicitly? If so, what data and rules are used?
- Does it use a broad-market index, moving averages, market breadth, volatility, trend structure, macroeconomic variables, or a statistical regime model?
- Does the strategy require a favorable general market before opening positions?
- Does it reduce exposure, stop opening new positions, hedge, or exit when the market deteriorates?
- Are different entry, exit, or risk rules used in different regimes?
- How does the paper confirm a regime change, and how quickly does the rule respond?

**Important:** QuantForge must preserve the paper's own market-regime definition. It must not impose a universal definition of a bull or bear market on every strategy.

If a paper does not include a general-market filter, record that fact. A market filter added later by QuantForge must be treated as an enhancement and tested separately.

### 2.3 Market Eligibility and Exposure Conditions

Beyond identifying a market regime, extract the conditions that determine whether the strategy is permitted to trade.

Capture, where applicable:

- Whether trading is allowed in every regime or only selected regimes.
- Whether new positions are restricted during unfavorable conditions.
- Whether gross or net exposure changes.
- Limits on simultaneous positions.
- Market-wide risk or volatility thresholds.
- Benchmark or market-index conditions.

Distinguish **market classification** from **the action taken because of that classification**.

### 2.4 Stock-Level Price Action, Volume, and Confirmations

For stock or instrument selection, QuantForge should capture the exact price-action and volume conditions used by the paper.

Possible categories include:

- Breakout from a base, range, or resistance.
- Trend structure, such as higher highs and higher lows.
- Pullbacks, retests, or support/resistance interactions.
- Reclaiming a level after a failed breakdown.
- Failed breakouts or failed breakdowns.
- Price and volume confirmation.
- Relative strength against a benchmark or peer group.
- Trend continuation or reversal conditions.
- Distribution, weakening momentum, or deterioration in relative strength.

These are examples of categories—not rules to automatically add to every strategy. QuantForge must extract the paper's actual conditions and record any interpretation or additional rule separately.

For each entry or exit condition, capture:

- The precise rule or formula.
- Required data and timeframe.
- Whether price, volume, relative strength, or another variable is involved.
- Confirmation required before action.
- Whether the condition is evaluated at close, intraday, or another time.
- Whether the paper uses discretionary judgment or a fully mechanical rule.

### 2.5 Position Sizing and Position Management

Position management is a mandatory research dimension because it materially affects strategy performance and risk.

QuantForge should extract, where applicable:

- Initial position sizing method.
- Risk per trade or capital allocation per position.
- Volatility-based or risk-based sizing.
- Maximum position size and exposure.
- Adding to winners or pyramiding.
- Reducing positions or scaling out.
- Partial profit-taking.
- Trailing stops and stop adjustments.
- Re-entry rules.
- Position holding limits or time-based exits.
- Rules for managing multiple simultaneous positions.

A paper that defines an entry signal but does not explain position sizing or subsequent position adjustments must be marked as **incomplete in position-management methodology**. QuantForge must not assume that an entry signal alone constitutes a complete strategy.

### 2.6 Risk Management

Risk management is mandatory for every strategy evaluation.

QuantForge should extract and document:

- Initial stop-loss or invalidation conditions.
- Maximum risk per trade.
- Maximum portfolio risk or exposure.
- Drawdown controls.
- Volatility or risk-based exposure adjustments.
- Correlation or concentration controls.
- Tail-risk controls, hedging, or protective rules.
- Risk limits that prevent new entries.
- Conditions that trigger reductions or liquidation.
- Risk assumptions used in the paper's backtest.

If a paper does not provide a risk-management framework, that omission must be explicit. QuantForge-added risk rules must be documented as enhancements, not attributed to the original paper.

### 2.7 Exit and Trade Management

QuantForge must analyze exits independently from entries.

Extract the paper's rules for:

- Stop-loss exits.
- Profit targets.
- Trailing stops.
- Trend or signal reversal exits.
- Breakdowns or failed breakouts.
- Time-based exits.
- Partial exits and scaling out.
- Market-regime exits.
- Re-entry after an exit.
- Any discretionary exit process.

The system should distinguish **entry logic**, **position adjustment logic**, and **exit logic**. If one of these is missing, the strategy specification must identify the gap.

### 2.8 Portfolio and Capital Management

Where applicable, capture how the paper manages capital across trades and instruments:

- Portfolio construction and allocation.
- Equal-weight, risk-weighted, or other allocation methods.
- Maximum number of positions.
- Exposure and concentration limits.
- Sector, asset, or factor constraints.
- Rebalancing rules.
- Cash management.
- Portfolio-level risk controls.
- Interaction between individual trade risk and total portfolio risk.

### 2.9 Research Rule Provenance

Every extracted strategy rule must carry a provenance label:

| Label | Meaning |
|---|---|
| **Explicit** | Directly stated by the paper or its documented methodology. |
| **Inferred** | Interpreted from the paper because the rule is not fully stated mechanically. The inference and supporting evidence must be recorded. |
| **QuantForge-added** | Introduced by QuantForge as a modification, assumption, risk control, or enhancement. |

The original paper methodology and any enhanced strategy must remain separate.

For example:

- **Original strategy:** Paper's breakout entry, market filter, sizing, and exit rules as reported.
- **Enhanced variant:** Original strategy plus an additional market filter, alternative stop, pyramiding rule, or partial profit-taking method.

Each variant must have its own specification, configuration, implementation version, and backtest results. Enhancements must be evaluated independently so that their impact can be measured.

### 2.10 Strategy Completeness

QuantForge should classify a paper or strategy by its primary contribution:

- Entry-focused.
- Exit-focused.
- Position-management-focused.
- Risk-management-focused.
- Market-regime-focused.
- Portfolio-management-focused.
- Complete strategy.
- Research or market-analysis contribution without a directly implementable trading strategy.

A strategy should not be labelled **fully implementable** unless its required operational rules are sufficiently specified—or all missing rules have been clearly identified and supplied as separately documented assumptions or QuantForge additions.

---

## 3. Research Discovery and Paper Evaluation

The initial development phase focuses on building a reliable research pipeline.

### 3.1 Research Scope

The research pipeline should cover both trading strategies and broader market research, including:

- Momentum, trend-following, breakout, and mean-reversion strategies.
- Market regimes and general-market analysis.
- Macroeconomics and business cycles.
- Market structure and behavioral finance.
- Relative strength and price/volume research.
- Risk management and position management.
- Portfolio theory, portfolio construction, and capital allocation.
- Research relevant to equities and cryptocurrency markets.

The topic taxonomy should be configurable rather than hardcoded.

### 3.2 Initial Research Sources

Initial sources:

- **arXiv** — academic papers and preprints.
- **OpenAlex** — scholarly metadata, works, authors, concepts, and open-access information.
- **SSRN** — working papers and finance-related research, using permitted interfaces and access methods.
- **Crossref** — DOI and bibliographic metadata cross-checking.

Research-source connectors should share a common interface so that additional sources can be added later.

### 3.3 Discovery Modes

QuantForge should support two discovery modes:

1. **Scheduled discovery:** Automatically discover research on a configurable weekly schedule.
2. **On-demand discovery:** Run a search manually for a topic, keyword, author, date range, or research question.

Scheduled discovery and on-demand discovery should use the same normalization, deduplication, filtering, evaluation, and storage services.

### 3.4 Candidate Processing Pipeline

The initial research pipeline is:

1. Discover paper metadata.
2. Normalize metadata across sources.
3. Deduplicate candidates.
4. Apply metadata and keyword filters.
5. Evaluate relevance to QuantForge's research scope.
6. Evaluate research credibility and methodological quality.
7. Evaluate implementability and reproducibility.
8. Calculate and store research-priority scores.
9. Rank candidates.
10. Acquire permitted paper files according to priority and limits.
11. Validate acquired documents.
12. Extract text and document structure.
13. Prepare a Research Document Package for strategy extraction.

Each stage must have a recorded status, timestamps, errors, and relevant outputs.

### 3.5 Deduplication

Deduplication should use a hierarchy of identifiers and normalized metadata:

- DOI.
- arXiv identifier.
- Source-specific identifier.
- Normalized title.
- Additional metadata checks where necessary.

A paper appearing in multiple sources should be represented as one research record with multiple source references.

### 3.6 Paper Evaluation and Ranking

Candidate evaluation should keep separate dimensions instead of hiding all judgments inside one score:

- **Relevance:** How closely the paper relates to configured research interests.
- **Credibility:** Evidence of source, publication status, author and venue context, and methodological transparency.
- **Implementability:** Whether the methodology can be translated into explicit computational rules.
- **Reproducibility:** Availability and clarity of data, assumptions, methodology, and reported results.
- **Research priority:** A configurable overall prioritization used to decide which papers to investigate or acquire.

Citation counts may be retained as contextual metadata but must not be treated as a direct measure of research quality.

Evaluation must distinguish facts found in metadata or the paper from LLM-generated assessments. Where possible, assessments should include evidence or a concise rationale.

### 3.7 Paper Acquisition Policy

Paper acquisition should be priority-based and bounded by configurable limits.

Initial suggested defaults:

- Maximum **20 papers per week**.
- Maximum **100 papers per month**.

These are configurable operational defaults, not permanent hardcoded limits. The system should support manual approval or automatic acquisition according to configured thresholds.

Acquisition should prefer permitted sources, such as:

- Official arXiv PDFs.
- Permitted SSRN downloads.
- Open-access locations identified through OpenAlex.
- Publisher or institutional repository copies where access is permitted.

QuantForge must not bypass access restrictions. If no permitted full-text copy is available, preserve metadata and mark the paper as unavailable for full-text processing.

### 3.8 Research Document Package

Each acquired paper should have a consistent package containing, where available:

```text
research_library/
└── <paper_id>/
    ├── metadata.json
    ├── paper.pdf
    ├── text.txt
    ├── sections.json
    ├── tables/
    ├── figures/
    └── acquisition.json
```

The package should preserve:

- Original paper file.
- Normalized metadata.
- Extracted text.
- Section structure.
- Relevant tables and figures where extraction is supported.
- Source and acquisition details.
- File hashes and processing status.

The exact directory layout may evolve, but the package must remain traceable to its research record.

### 3.9 Research State Tracking

The research pipeline should maintain explicit states, such as:

```text
DISCOVERED
  → CANDIDATE
  → DEDUPLICATED
  → RELEVANCE_CHECKED
  → QUALITY_CHECKED
  → RANKED
  → ACQUISITION_PENDING
  → DOWNLOADED
  → PARSED
  → READY_FOR_EXTRACTION
```

Failure, rejection, unavailable-document, and retry states should be recorded rather than silently discarded.

The state model may be refined during implementation, but transitions and failure reasons must be explicit.

---

## 4. AI and Deterministic Responsibilities

QuantForge should use AI selectively and keep quantitative computation deterministic.

### 4.1 Appropriate LLM Responsibilities

An LLM may assist with:

- Research relevance classification.
- Paper summaries and methodology interpretation.
- Extraction of strategy rules into structured schemas.
- Identification of missing or ambiguous methodology.
- Evidence-linked explanations.
- Drafting implementation plans or code.
- Code review and debugging assistance.
- Research reports and comparisons.

### 4.2 Deterministic Python Responsibilities

Python services must handle:

- Metadata normalization and deduplication.
- Rule-based filtering and scoring.
- Data processing and validation.
- Indicator and signal calculations.
- Position sizing and risk calculations.
- Portfolio accounting and allocation.
- Backtesting and trade simulation.
- Performance metrics.
- Robustness and sensitivity testing.
- Budget enforcement and usage accounting.

LLMs must not be relied upon for arithmetic, portfolio accounting, or authoritative backtest results.

### 4.3 Central LLM Budget and Usage Controls

All LLM requests should pass through a centralized budget and usage-management service.

The service should support:

- Configurable provider and model selection.
- Per-request and per-task limits.
- Input and output token limits.
- Per-paper call limits.
- Concurrency limits.
- Retry limits.
- Usage and cost logging.
- Budget reservations before requests.
- Budget stops when limits are reached.
- No automatic credit recharge.
- Clear handling of budget or provider errors.

The initial implementation should use conservative settings and allow configuration changes without code modifications. Actual provider pricing and account-level controls must be verified when implementation begins.

---

## 5. Initial Technical Direction

The following is the intended initial direction. Specific libraries and implementation choices may be refined during development.

| Area | Initial direction |
|---|---|
| Main language | Python |
| Metadata and pipeline storage | PostgreSQL |
| Paper and document storage | Local filesystem initially |
| Research APIs | arXiv, OpenAlex, Crossref, permitted SSRN access |
| HTTP | `httpx` |
| Data validation and schemas | Pydantic |
| PDF text extraction | PyMuPDF |
| Table extraction | pdfplumber where needed |
| LLM access | OpenRouter, behind a central budget manager |
| Testing | pytest |
| Logging | Python logging with structured/contextual records |
| Initial interface | CLI |
| Later interface | Dashboard and MCP tools, after core services are stable |

### 5.1 Architectural Boundaries

- Research discovery and evaluation should be independent of paper processing.
- Paper processing should be independent of strategy extraction.
- Strategy specifications should be independent of backtest engine implementation.
- Original strategy versions should be independent of enhanced variants.
- LLM provider integration should be isolated behind a service interface.
- Research-source connectors should use a common interface.
- PostgreSQL should store metadata, states, scores, and relationships; the filesystem should store document artifacts initially.
- MCP should expose existing services as tools. It should not contain the core research or trading logic.

### 5.2 Initial Interface Strategy

Development should begin with a **CLI-first** workflow. This keeps the initial system testable and avoids building a dashboard before the underlying services are reliable.

A future dashboard may provide:

- Research discovery and candidate review.
- Paper metadata and document access.
- Research scores and evaluation reports.
- Strategy specifications and versions.
- Backtest and validation reports.
- Research pipeline status and budget usage.

---

## 6. Research Phase — Scope and Completion Criteria ✓ COMPLETE

The research pipeline phase is complete as of November 2026. All exit criteria were met and verified. See `PHASE_GATE.md` for the full sign-off record.

### 6.1 Initial Research Phase Deliverables

- Configurable research topics and discovery settings.
- Source connectors for arXiv, OpenAlex, Crossref, and permitted SSRN workflows.
- Common metadata model.
- Deduplication service.
- Candidate filtering and evaluation.
- Separate relevance, credibility, implementability, reproducibility, and priority scores.
- Rank-based paper acquisition with configurable limits.
- PDF validation and hashing.
- Text and section extraction.
- Research Document Package format.
- PostgreSQL persistence and research-state tracking.
- CLI commands for discovery, evaluation, acquisition, processing, and status.
- Logging, error handling, tests, and configuration documentation.
- Centralized LLM usage and budget controls for any LLM-assisted evaluation.

### 6.2 Initial Research Phase Acceptance Criteria

The research phase should be considered complete when:

1. A user can run an on-demand research search from the CLI.
2. Scheduled discovery can be configured and executed.
3. Results from supported sources are normalized into a common schema.
4. Duplicate records are identified and consolidated.
5. Candidate filters and evaluation scores are reproducible and stored.
6. Candidates can be ranked and acquired according to configurable limits.
7. Acquired files are validated, hashed, and linked to their metadata.
8. PDFs can be processed into text and section data where extraction is supported.
9. Research records have clear processing states and failure reasons.
10. A paper's metadata, source, original file, extracted text, and processing history can be retrieved.
11. LLM usage is logged and constrained by configured budgets.
12. The pipeline is covered by automated tests and can be run without a dashboard.

---

## 7. Later Phases

QuantForge is developed incrementally. Phases are specified when the prior phase is complete and requirements are clarified.

### Phase 2 — Strategy Engineering (Current)

Specified and in progress. See `ROADMAP_STRATEGY_ENGINEERING.md` and `STRATEGY_SCHEMA.md`.

Scope: Extract structured Strategy Specifications from Research Document Packages using a three-call LLM extraction pipeline. Produce provenance-tracked, human-reviewed specs ready for backtesting.

### Phase 3 and Beyond — Not Yet Specified

The following phases are expected but not yet specified. They will be planned after Strategy Engineering is complete:

- Market and instrument data ingestion.
- Backtesting engine and realistic execution assumptions.
- Risk, position, and portfolio simulation.
- Robustness, sensitivity, and out-of-sample validation.
- Strategy comparison and research reports.
- Paper trading.
- Controlled live deployment and operational safeguards.
- Dashboard and MCP tool exposure (after all core phases are stable).

Each phase will be added to this document and given its own roadmap when its requirements are clarified. No phase should prematurely implement functionality belonging to a later phase.

---

## 8. Project Documentation and Change Discipline

`PROJECT_SPEC.md` is the primary statement of QuantForge's intent, scope, principles, and phase-level requirements.

As the project grows, supporting documents may be introduced, such as:

- `ROADMAP.md` — milestones, phase order, and progress.
- `ARCHITECTURE.md` — components, boundaries, and data flow.
- `DATA_MODEL.md` — database entities and relationships.
- `RESEARCH_SCHEMA.md` — research and paper metadata schemas.
- `STRATEGY_SCHEMA.md` — structured strategy methodology schema.
- `AGENTS.md` — coding-agent instructions and project conventions.
- `DECISIONS.md` — important architectural decisions and rationale.

These supporting documents should complement—not contradict—this specification. Significant changes to scope, principles, or phase requirements should be reflected in this file.

---

## 9. Current Project Status

- **Project:** QuantForge
- **Current phase:** Strategy Engineering (Phase 2)
- **Research phase:** Complete — 511 tests passing, all exit criteria met (November 2026)
- **Strategy Engineering:** Roadmap and schema defined, Milestone 12 starting
- **Next implementation focus:** Strategy data models, database migration, and the three-call LLM extraction pipeline.
