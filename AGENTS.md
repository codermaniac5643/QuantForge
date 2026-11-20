# QuantForge — Agent Instructions

**Document:** `AGENTS.md`
**Phase:** Strategy Engineering (Phase 2)
**Research Phase:** Complete (M0–M11, 511 tests passing)

---

## 1. Read First

Before writing any code:

1. `PROJECT_SPEC.md` — intent, principles, scope.
2. `ROADMAP_STRATEGY_ENGINEERING.md` — current milestones and exit criteria.
3. `STRATEGY_SCHEMA.md` — authoritative output format for all extraction work.
4. Relevant source code before editing any existing component.

---

## 2. Current Scope

**Strategy Engineering only.** Extract structured Strategy Specifications from Research Document Packages.

**Allowed:**
- Strategy Specification models and DB persistence.
- Three-call LLM extraction pipeline (routing → extraction → validation).
- Section quality filter (`min_section_chars = 200`) and extraction audit block.
- Spec writer and post-extraction provenance checks.
- Review workflow and status transitions.
- Variant management for `quantforge_added` rules.
- CLI commands, prompt templates, tests, documentation.
- Config in `config/strategy.json`; model/token settings in `config/llm.json` under `strategy_extraction`.

**Research Phase services** (connectors, acquisition, PDF processing, budget manager, repository layer) are complete. Use them as-is. Only modify for an explicit bug fix.

**Not allowed:**
- Backtesting, market data, position sizing, portfolio simulation.
- Paper trading, broker integrations, live trading.
- Dashboard or MCP server.
- ML components not explicitly requested.
- Modifications to the Research Phase pipeline without explicit justification.

---

## 3. Architecture Boundaries

- Strategy extraction is separate from document processing.
- Spec writer does not call the LLM — it validates and assembles extraction output.
- LLM calls go through the existing budget manager. Never call a provider directly.
- Repository layer is the only DB access point.
- Configuration is in JSON files. No hardcoded limits, model names, or paths.
- MCP, when introduced later, exposes services — it does not contain business logic.

---

## 4. Extraction Rules

These apply to all LLM extraction work:

- **Use `sections.json` for routing.** Never send full paper text to the LLM.
- **Filter short sections before routing.** Sections under `min_section_chars` (default 200) are skipped and recorded in `sections_filtered_too_short`, not silently dropped.
- **Full-text fallback** triggers only when all sections are filtered. Log a warning.
- **Three calls per paper:** routing → pillar extraction (grouped) → validation + assessment.
- **Call 3 must check for cross-pillar leakage** — rules assigned to the wrong pillar during extraction.
- **Write an extraction audit block** after extraction completes: routing map, sections used, sections skipped, sections filtered, token counts per call, extraction date.
- **Prompts must instruct the LLM** to return only JSON, omit optional fields when the paper provides nothing, and not write null or placeholder values.
- **Budget exhaustion** is a controlled stop. Save partial spec as `draft`, record which pillars completed.

---

## 5. Provenance — Non-Negotiable

Every rule carries one label: `explicit`, `inferred`, or `quantforge_added`.

- `inferred` rules require an `evidence` field. No exceptions.
- `quantforge_added` rules must never appear in a base spec. Variants only.
- Post-extraction checks enforce this before any spec is written to disk. A violation raises an error — it is never silently saved.

---

## 6. LLM and Cost Controls

- All calls go through the budget manager. No direct provider calls.
- No hardcoded API keys or secrets.
- No automatic credit recharge.
- No indefinite retries.
- Mock LLM responses in all routine tests. Real calls are opt-in only.
- Use deterministic Python for anything that does not require language understanding.

---

## 7. Code Standards

- Typed, modular Python. Type hints on all public functions.
- Pydantic for all models and config validation.
- Focused functions. No unnecessary abstraction.
- No global mutable state.
- Explicit exceptions with useful messages. No silent suppression.
- Context managers for resources.
- No placeholder implementations, dead code, unexplained TODOs, or unused dependencies.
- No new dependency without explaining why it is needed.

---

## 8. Configuration

- Adjustable settings in `config/strategy.json` (extraction budgets, filters) and `config/llm.json` (model, token limits under `strategy_extraction`).
- Credentials in environment variables or approved secrets mechanism.
- Never commit `.env`, keys, tokens, or passwords.
- Validate config at startup with clear error messages.

---

## 9. Database and Filesystem

- Use the repository layer for all DB access.
- Use Alembic migrations for schema changes. Update `DATA_MODEL.md`.
- Use transactions where consistency is required.
- Uniqueness constraints on all records where duplicates would be a problem.
- Spec JSON files on filesystem; paths and metadata in DB.
- Rerunning extraction must not duplicate records or overwrite an approved spec without incrementing the version.

---

## 10. Testing

Every meaningful change includes tests.

- **Unit:** models, provenance checks, section filter, extraction grouping, spec assembly, state transitions.
- **Integration:** DB repository, extraction pipeline end-to-end, review workflow.
- **Fixtures:** stable mocked LLM responses and sample `sections.json` files.
- **Failure cases:** malformed LLM JSON, budget exhaustion mid-paper, all sections filtered, missing `sections.json`.
- **Real LLM calls:** opt-in, clearly separated, never run by default.

Do not mark a task complete because the happy path works.

---

## 11. Review Workflow Requirements

A spec cannot reach `approved` without a human confirming:

1. Every rule has a provenance label.
2. Every `inferred` rule has evidence.
3. No `quantforge_added` rule in the base spec.
4. Missing pillars are recorded, not skipped.
5. Completeness classification matches actual coverage.
6. Assessment is consistent with extracted content.

`quantforge strategy review show <id>` must support `--output-file` for specs with many rules, writing a Markdown review sheet to disk.

---

## 12. Workflow Per Task

1. **Read** — project documents and existing code for the affected area.
2. **Scope** — identify the milestone, what is in and out of scope.
3. **Plan** — propose a short implementation plan before writing code for non-trivial changes.
4. **Implement** — smallest coherent change that satisfies the task.
5. **Test** — run tests, report any not run.
6. **Review** — correctness, provenance, config, scope.
7. **Document** — update roadmap checkboxes and relevant docs.
8. **Report** — files changed, behavior added, test results, remaining issues.

Ask before making a consequential architectural choice not covered by project documents.

---

## 13. Definition of Done

A task is complete only when:

- Requested behavior is implemented.
- Tests pass, or unrun tests are explicitly reported.
- Error paths and config are handled.
- No secrets or unrelated files introduced.
- Docs updated where needed.
- Scope stays within Strategy Engineering.
- Actual changes and verification results are reported.

---

## 14. Current Instruction

**Work on Strategy Engineering only.**

Read `STRATEGY_SCHEMA.md` before touching any extraction, model, or spec writer code.
Start from the next incomplete milestone in `ROADMAP_STRATEGY_ENGINEERING.md`.
Do not begin backtesting or any future phase without explicit approval.