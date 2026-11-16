# QuantForge — Research Phase Gate

**Date:** November 2026  
**Phase:** Research (Milestones 0–11)  
**Status:** COMPLETE  
**Tests:** 511 passing, 0 failures  

---

## Exit Criteria Checklist

### Discovery & Normalization
- [x] On-demand discovery works (arXiv, OpenAlex connectors verified)
- [x] Source metadata normalized into canonical ResearchPaper model
- [x] Papers deduplicated by DOI, arXiv ID, and normalized title
- [x] Multiple source references preserved per paper
- [x] Raw metadata and provenance stored for every record

### Evaluation & Ranking
- [x] Candidate evaluation across 5 dimensions (relevance, credibility, implementability, reproducibility, priority)
- [x] All scores are deterministic and explainable — evidence strings on every dimension
- [x] LLM used only for priority dimension, behind budget manager
- [x] Scores persisted to database with evaluation JSON
- [x] Composite score computed with configurable weights

### Acquisition
- [x] Acquisition limits enforced (weekly/monthly caps checked against DB)
- [x] Approval mode respected (manual/auto configurable)
- [x] PDF resolution chain: arXiv → OpenAlex OA → Unpaywall → Semantic Scholar
- [x] Downloaded files validated (magic bytes, size, SHA-256 hash)
- [x] Atomic file placement — no partial files left on disk
- [x] Acquisition provenance recorded in evaluation JSON

### PDF Processing
- [x] Full text extracted with page boundary markers
- [x] Section headings detected deterministically (known names, numeric prefixes, ALL CAPS)
- [x] TOC entries filtered from sections
- [x] Duplicate headings deduplicated — richer content wins
- [x] Research Document Package assembled atomically (6 files)
- [x] Re-run safe — PROCESSED papers can be reprocessed

### Pipeline Orchestration
- [x] State machine enforced at repository level — invalid transitions blocked
- [x] Pipeline resumable from last successful state — completed stages skipped
- [x] Per-paper stage failures do not stop other papers
- [x] Dry-run mode skips acquisition and LLM without affecting other stages
- [x] Stage timing recorded in StageResult

### LLM Controls
- [x] All LLM calls go through BudgetManager — never called directly
- [x] Hard budget cap enforced before every call
- [x] Per-paper call limit enforced
- [x] Budget exhaustion is a controlled stop — not a crash
- [x] Every call logged with tokens, cost, task, paper ID
- [x] LLM provider isolated behind abstract interface — swappable

### CLI
- [x] quantforge research status
- [x] quantforge research rank
- [x] quantforge research process
- [x] quantforge research report (terminal, markdown, JSON)
- [x] All commands tested with mocked dependencies

### Database & Storage
- [x] PostgreSQL schema with proper indexes and unique constraints
- [x] Alembic migrations — schema changes are versioned and reversible
- [x] Repository layer is the only DB access point
- [x] PDFs stored on filesystem — only paths stored in DB
- [x] Integration tests run against real DB with rollback isolation

---

## Known Limitations

### Section Extraction
- Paper titles and author names on page 1 may be detected as headings
  (title-case detection disabled; known-headings and numeric-prefix detection only)
- Date strings (e.g. "12 Aug 2011") detected as numeric-prefix headings in papers
  with embedded case transcripts or timelines
- Table of contents entries for multi-word headings may appear when dot-leader
  density filter does not catch all TOC formats
- **Impact:** Low — core academic sections (Abstract, Introduction, Methods,
  Results, Conclusion, References) are always captured correctly with full content

### Discovery
- arXiv category filter uses explicit category list (q-fin.PM, q-fin.ST, q-fin.TR,
  q-fin.GN, q-fin.RM) — wildcard `q-fin.*` not supported by arXiv API
- OpenAlex connector does not paginate beyond first page (max 200 results per query)
- SSRN: metadata discovery only — no PDF acquisition (login required)
- Crossref: metadata only — acquisition via DOI → Unpaywall/Semantic Scholar chain

### Scoring
- Relevance scorer relies on keyword matching — papers using different terminology
  for the same concept may score lower than expected
- Credibility scorer uses citation count as a proxy — new papers always score lower
  regardless of quality
- Priority dimension requires LLM — papers evaluated without LLM (budget exhausted,
  disabled) have no priority score and rank by composite of 4 dimensions only

### Pipeline
- Scheduled/recurring discovery not yet implemented — CLI triggers only
- No web dashboard — CLI and report files only
- Single-node only — no distributed processing

---

## Deferred to Next Phase

The following are explicitly out of scope for the research phase:

- Strategy extraction from Research Document Packages
- Backtesting engine and market data ingestion
- Position sizing and portfolio simulation
- Paper trading and broker integrations
- Live trading and autonomous agents
- MCP server/tool exposure
- Web dashboard

---

## Test Coverage Summary

| Milestone | Tests | Type |
|---|---|---|
| 0 — Foundation | 1 | unit |
| 1 — Config & Models | 42 | unit |
| 2 — Database | 18 | integration |
| 3 — Connectors | 18 | unit (mocked HTTP) |
| 4 — Normalization | 78 | unit |
| 5 — Filtering | 125 | unit |
| 6 — LLM | 27 | unit (mocked provider) |
| 7 — Acquisition | 45 | unit (mocked HTTP) |
| 8 — PDF Processing | 85 | unit |
| 9 — Orchestration | 28 | unit |
| 10 — CLI | 28 | unit (mocked DB) |
| 11 — Integration | 25 | integration (real DB) |
| **Total** | **511** | |

---

## Sign-off

Research phase complete. All exit criteria met. Known limitations documented.
Ready to begin Phase 2 specification.
