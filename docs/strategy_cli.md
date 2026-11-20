# docs/strategy_cli.md
# Strategy Engineering CLI Reference

This document covers all `quantforge strategy` commands introduced in Phase 2 (Strategy Engineering).

---

## Overview

```
quantforge strategy extract single <paper_id>   Run extraction for one paper
quantforge strategy extract batch               Run extraction for all eligible papers
quantforge strategy status                      Summary of all specs by status
quantforge strategy report <paper_id>           Full spec report for one paper
quantforge strategy report --approved           Report all approved base specs
quantforge strategy review list                 List specs pending review
quantforge strategy review show <id>            Show a spec with checklist and flagged items
quantforge strategy review approve <id>         Approve a spec
quantforge strategy review reject <id>          Reject a spec with notes
quantforge strategy variant create <id>         Create a named variant from an approved base spec
quantforge strategy variant list <paper_id>     List all variants for a paper
quantforge strategy variant show <id>           Show a variant and its added rules
```

All commands support `--help` for full option documentation.

---

## Extraction

### `quantforge strategy extract single <paper_id>`

Runs the full three-call LLM extraction pipeline for one paper and writes the resulting `StrategySpecification` to disk and database.

**Requirements:**
- The paper must exist in the database.
- The paper's Research Document Package must exist at `<library-root>/<paper_id>/`.

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--config-dir` | `config` | Path to the config/ directory |
| `--library-root` | `research_library` | Root directory for the research library |
| `--json` | false | Output result as JSON |

**Example:**

```bash
quantforge strategy extract single 3f2d1b4a-... --library-root research_library
```

**Outcomes:**

| Outcome | Meaning |
|---------|---------|
| `success` | Full spec written to disk and DB |
| `partial` | Spec written but budget exhausted mid-extraction — some pillars incomplete |
| `extraction_failed` | No spec could be assembled (all sections filtered or routing failed) |
| `provenance_error` | Spec assembled but failed post-extraction provenance checks |
| `budget_exhausted` | Budget exhausted before extraction could start |
| `pipeline_error` | Unexpected error during the pipeline |

Exit code is non-zero for all failure outcomes.

---

### `quantforge strategy extract batch`

Runs extraction for all papers in a given pipeline state (default: `processed`).

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--state` | `processed` | PaperState to target (processed \| approved \| acquired) |
| `--limit` | `0` (no limit) | Max papers to process |
| `--skip-existing` / `--no-skip-existing` | skip | Skip papers that already have a base spec |
| `--config-dir` | `config` | Path to config/ directory |
| `--library-root` | `research_library` | Root directory for research library |
| `--json` | false | Output JSON summary of all results |

**Example:**

```bash
# Extract all processed papers, skip those already extracted
quantforge strategy extract batch --state processed

# Force re-extraction of all processed papers (up to 5)
quantforge strategy extract batch --state processed --no-skip-existing --limit 5

# JSON output for scripting
quantforge strategy extract batch --json
```

The batch command prints progress to stderr and a summary table to stdout. Exit code is non-zero if any paper had a non-success/partial/skipped outcome.

---

## Status

### `quantforge strategy status`

Shows a summary of all strategy specifications grouped by status.

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--json` | false | Output as JSON |

**Example:**

```bash
quantforge strategy status
quantforge strategy status --json
```

**Sample output:**

```
📋  Strategy Engineering Status — 12 spec(s) in database

status          count
--------------  -----
approved        5
draft           4
under_review    2
superseded      1

  ⏳  Pending review      : 2
  🚩  Flagged rules (base): 3
```

**JSON fields:**

| Field | Description |
|-------|-------------|
| `total_specs` | Total spec records in DB |
| `by_status` | Count per status value |
| `pending_review` | Specs in `under_review` state |
| `flagged_rules` | Rules in active base specs needing review (inferred without evidence, or quantforge_added) |

---

## Report

### `quantforge strategy report <paper_id>`

Generates a full human-readable strategy specification report for one paper. Reports the latest non-superseded/non-rejected base spec.

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--approved` | false | Report all approved base specs (ignores `paper_id`) |
| `--output-file <path>` | stdout | Write report to file (`.md` renders Markdown, `.json` with `--json` renders JSON) |
| `--json` | false | Output as JSON |
| `--config-dir` | `config` | Path to config/ directory |
| `--library-root` | `research_library` | Root directory for research library |

**Examples:**

```bash
# Terminal report for one paper
quantforge strategy report 3f2d1b4a-...

# Save Markdown report to disk
quantforge strategy report 3f2d1b4a-... --output-file reports/my_paper.md

# JSON for one paper
quantforge strategy report 3f2d1b4a-... --json

# All approved specs as a combined Markdown file
quantforge strategy report --approved --output-file reports/all_approved.md
```

**Report sections:**

1. Header — paper title, IDs, status, completeness class
2. Component Coverage table — present / partial / absent per pillar
3. Missing Components — pillars not found, with impact and recommended action
4. Methodology Assessment — implementability, strengths, weaknesses
5. Extracted Rules — per-pillar rule list with provenance icons and evidence
6. Extraction Audit — tokens used, sections routed, sections filtered

---

## Review

### `quantforge strategy review list`

Lists all specs currently in `under_review` status.

```bash
quantforge strategy review list
quantforge strategy review list --json
```

---

### `quantforge strategy review show <strategy_id>`

Shows the full spec with the six-point review checklist and flagged items highlighted.

```bash
quantforge strategy review show 3f2d1b4a-...

# Save a Markdown review sheet to disk
quantforge strategy review show 3f2d1b4a-... --output-file review_sheet.md
```

---

### `quantforge strategy review approve <strategy_id>`

Approves a spec after confirming the checklist passes. Requires `--reviewer-id`.

```bash
quantforge strategy review approve 3f2d1b4a-... --reviewer-id alice
```

Fails if any checklist item fails (use `--force` to override after deliberate review).

---

### `quantforge strategy review reject <strategy_id>`

Rejects a spec with mandatory notes explaining the reason.

```bash
quantforge strategy review reject 3f2d1b4a-... --reviewer-id alice --notes "Entry rules incomplete"
```

---

## Variants

### `quantforge strategy variant create <strategy_id>`

Creates a named variant from an **approved** base spec. The base spec is never modified. Variants hold only `quantforge_added` rules.

```bash
quantforge strategy variant create 3f2d1b4a-... \
  --variant-name with_exit_rules \
  --description "Adds explicit exit rules not in the paper" \
  --rationale "Paper implied exit at peak; we make it explicit"
```

The command creates a scaffold JSON file. Edit it to add `quantforge_added` rules.

---

### `quantforge strategy variant list <paper_id>`

Lists all variants for a paper.

```bash
quantforge strategy variant list 3f2d1b4a-...
quantforge strategy variant list 3f2d1b4a-... --json
```

---

### `quantforge strategy variant show <strategy_id>`

Shows a variant with its added rules highlighted.

```bash
quantforge strategy variant show 7a1c3f88-...
quantforge strategy variant show 7a1c3f88-... --json
```

---

## Configuration

Strategy extraction uses two config files:

| File | Purpose |
|------|---------|
| `config/strategy.json` | Extraction behavior: `min_section_chars`, `max_section_tokens`, `token_budgets`, `pillar_groups` |
| `config/llm.json` | LLM provider, model, rate limits, spend budget |

Credentials must be in environment variables — never in config files.

---

## Workflow Example

```bash
# 1. Check what's in the pipeline
quantforge strategy status

# 2. Extract a paper
quantforge strategy extract single 3f2d1b4a-...

# 3. Review the extracted spec
quantforge strategy report 3f2d1b4a-...

# 4. Submit for review
quantforge strategy review list   # see what's pending

# 5. Show and approve
quantforge strategy review show <strategy_id>
quantforge strategy review approve <strategy_id> --reviewer-id alice

# 6. Create a variant with added rules
quantforge strategy variant create <strategy_id> \
  --variant-name adjusted_exit \
  --description "Explicit 2% trailing stop" \
  --rationale "Paper implied a stop; we make it concrete"

# 7. Inspect variant
quantforge strategy variant show <variant_strategy_id>
```
