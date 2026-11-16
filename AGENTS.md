# QuantForge — Agent Instructions

**Document:** `AGENTS.md`  
**Project:** QuantForge  
**Applies to:** All coding agents working in this repository  
**Current implementation scope:** Strategy Engineering phase (Phase 2)

---

## 1. Read the Project Documents First

Before planning or modifying code, read:

1. `PROJECT_SPEC.md` — project intent, principles, research requirements, and scope.
2. `ROADMAP.md` — current milestones, tasks, and acceptance criteria.
3. Any relevant architecture, data-model, schema, or decision documents that exist.
4. The relevant source code and tests before editing an existing component.

Do not assume that a task mentioned in a future phase is currently authorized for implementation.

## 2. Current Scope: Strategy Engineering Phase

QuantForge is being implemented incrementally. The Research Phase (M0–M11) is complete. **The current scope is Strategy Engineering only — extracting structured Strategy Specifications from Research Document Packages.**

Agents may implement the following:

- Strategy Specification data models and database persistence.
- Three-call LLM extraction pipeline (section routing, pillar extraction, validation).
- Spec writer service and post-extraction provenance validation.
- Review workflow and status transitions.
- Variant management for `quantforge_added` rules.
- CLI commands for extraction, review, variants, status, and reports.
- Prompt templates under `llm/prompts/strategy/`.
- Strategy configuration (`config/strategy.json`).
- Tests and documentation for all of the above.

All existing Research Phase services (connectors, acquisition, PDF processing, budget manager, repository layer) are available and should be used as-is. Do not modify them unless a bug fix is explicitly required.

### Out of scope unless explicitly approved in a later phase

Do not implement these merely because they are part of QuantForge's long-term vision:

- Backtesting engine or market data ingestion.
- Position sizing, portfolio simulation, or trade execution engines.
- Strategy optimization or robustness testing.
- Paper trading.
- Broker or exchange integrations.
- Live trading or autonomous trading agents.
- Dashboard development.
- MCP server/tool exposure.
- Unrequested machine-learning components.
- Any modification to the Research Phase pipeline beyond explicit bug fixes.

If a task appears to require a future-phase component, explain the dependency and defer it. Do not silently expand scope.

## 3. Preserve the Research and Trading Architecture

Follow these boundaries:

- Research discovery is separate from paper acquisition.
- Acquisition is separate from document processing.
- Document processing is separate from future strategy extraction.
- Source connectors must not own pipeline orchestration.
- Database repositories must not contain source-specific scraping logic.
- LLM provider code must be isolated behind a service interface.
- MCP, when introduced later, should expose existing services rather than contain core business logic.
- Configuration must not be scattered across modules.
- Research metadata belongs in PostgreSQL; paper artifacts belong in the configured filesystem storage initially.

Keep components modular, testable, and independently replaceable.

## 4. Research Integrity and Provenance

Research records and assessments must be traceable.

- Preserve source identifiers, source URLs, retrieval timestamps, and relevant metadata.
- Do not fabricate missing metadata, paper content, authors, publication status, or results.
- Distinguish metadata facts from generated evaluations.
- Store evaluation rationale or supporting evidence when available.
- Keep original paper files unchanged.
- Record hashes and processing versions for acquired artifacts.
- Preserve multiple source references when one paper is discovered in multiple sources.
- Record failures, unavailable documents, and processing errors rather than silently dropping records.
- Respect access restrictions and use only permitted acquisition methods.

For later strategy-related work, the project specification requires explicit separation between **paper-explicit**, **inferred**, and **QuantForge-added** rules. Do not attribute QuantForge assumptions or enhancements to an original paper.

## 5. LLM Usage and Cost Controls

All LLM calls must go through the centralized LLM service and budget manager.

Agents must not:

- Call a provider directly from research modules.
- Hardcode API keys or secrets.
- Add automatic credit recharge.
- Bypass budget reservations or usage logging.
- Retry indefinitely.
- Send entire papers when a smaller relevant excerpt is sufficient.
- Use an LLM for deterministic calculations or operations better handled by Python.

Agents should:

- Use LLMs only when language understanding or reasoning adds value.
- Prefer deterministic filtering, normalization, deduplication, and scoring where practical.
- Respect configured model, token, call, concurrency, and budget limits.
- Handle budget exhaustion as a controlled stop condition.
- Use mocked LLM responses in routine tests.
- Make real provider tests opt-in and clearly documented.

## 6. Python and Code Quality Standards

- Use the project's configured Python version and dependency manager.
- Prefer clear, typed, modular Python code.
- Use type hints for public functions and important data structures.
- Use Pydantic for validated external/configuration data where appropriate.
- Keep functions focused and avoid unnecessary abstraction.
- Avoid global mutable state.
- Use explicit exceptions and useful error messages.
- Do not suppress exceptions without recording or handling them.
- Use context managers for resources.
- Avoid unnecessary dependencies.
- Do not introduce a framework or library without explaining why it is needed.
- Follow the existing style and naming conventions.
- Do not create placeholder implementations that appear complete.
- Do not leave dead code, unexplained TODOs, or unused dependencies.

## 7. Configuration and Secrets

- Put adjustable research settings in configuration files.
- Keep credentials and secrets in environment variables or an approved secret mechanism.
- Never commit `.env`, API keys, tokens, passwords, or private credentials.
- Maintain `.env.example` with placeholder values only.
- Validate configuration at startup.
- Provide safe defaults and clear validation errors.
- Do not hardcode weekly/monthly acquisition limits, model names, paths, or token limits where they are intended to be configurable.

## 8. Database and Filesystem Rules

- Use the database repository layer for persistence.
- Use migrations for schema changes.
- Do not modify database schema without updating migrations and relevant documentation.
- Use transactions for operations that must remain consistent.
- Define uniqueness constraints where needed to protect against duplicate records.
- Avoid storing large PDF or text artifacts directly in PostgreSQL unless explicitly approved.
- Store artifact paths and metadata in the database.
- Use safe temporary files and atomic placement for downloads.
- Do not delete research artifacts or database records without an explicit requirement.
- Ensure rerunning a pipeline stage does not unnecessarily duplicate records or files.

## 9. Testing Requirements

Every meaningful change should include appropriate tests.

- **Unit tests:** models, normalization, filters, scoring, state transitions, utilities.
- **Integration tests:** database repositories, connectors, acquisition, processing, and pipeline behavior.
- **Fixtures:** use stable sample responses and test documents.
- **External services:** mock by default.
- **Real API tests:** opt-in, limited, and clearly separated from normal test runs.
- **LLM tests:** mock responses for routine tests; real calls only when explicitly enabled.
- **Failure tests:** cover rate limits, timeouts, malformed responses, missing files, invalid PDFs, and budget exhaustion where relevant.

Do not mark a task complete merely because its happy path works.

## 10. Documentation and Roadmap Discipline

When implementing a milestone:

1. Identify the relevant milestone and acceptance criteria in `ROADMAP.md`.
2. Confirm dependencies and current repository state.
3. Implement only the necessary scope.
4. Add or update tests.
5. Update documentation and configuration examples.
6. Report what changed and how it was verified.
7. Update roadmap checkboxes only when the work and tests satisfy the stated criteria.

Keep documentation accurate:

- Update `PROJECT_SPEC.md` when agreed project scope or principles change.
- Update `ROADMAP.md` when milestones, tasks, dependencies, or acceptance criteria change.
- Add detailed technical documents when needed rather than overloading the main specification.
- Record significant architectural decisions and their rationale in `DECISIONS.md` when that file is introduced.

Do not mark work complete based solely on code generation or file creation.

## 11. Git and Change Safety

- Inspect the working tree before making changes.
- Do not overwrite unrelated user changes.
- Do not delete files or data without explicit justification.
- Keep changes focused on the requested task or milestone.
- Avoid committing generated PDFs, research-library artifacts, logs, caches, or secrets.
- Do not create a commit unless requested.
- Explain migrations or potentially destructive operations before running them.
- Do not claim commands or tests passed unless they were actually executed.

## 12. Agent Workflow for Each Task

For each coding task, follow this workflow:

1. **Understand:** Read relevant project documents and inspect existing code.
2. **Scope:** Identify the milestone, requirements, dependencies, and out-of-scope items.
3. **Plan:** Propose a short implementation plan for non-trivial changes.
4. **Implement:** Make the smallest coherent change that satisfies the task.
5. **Test:** Run relevant tests and report any tests not run.
6. **Review:** Check correctness, security, configuration, and scope.
7. **Document:** Update relevant documentation and roadmap progress.
8. **Report:** Summarize files changed, behavior added, test results, and remaining issues.

Ask for clarification before making a consequential architectural choice that is not covered by the project documents. For minor implementation choices, follow existing conventions and document the decision when useful.

## 13. Definition of Done

A task is complete only when:

- Its requested behavior is implemented.
- Relevant tests pass, or unrun tests are explicitly reported.
- Error paths and configuration are handled appropriately.
- No secrets or unrelated files were introduced.
- Documentation is updated where needed.
- The implementation remains within the current research-phase scope.
- The agent reports the actual changes and verification results.

---

## 14. Current Agent Instruction

**Work on the Strategy Engineering Phase only.** The Research Phase is complete.

Start from the next incomplete milestone in `ROADMAP_STRATEGY_ENGINEERING.md`. Read `STRATEGY_SCHEMA.md` before implementing any extraction, model, or spec writer code — it is the authoritative definition of the output format.

Respect milestone dependencies and exit criteria. Do not begin backtesting or any other future phase without explicit approval.
