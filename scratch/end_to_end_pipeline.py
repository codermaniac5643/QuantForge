#!/usr/bin/env python3
"""
end_to_end_pipeline.py
======================
Runs one real paper through every *built* pipeline stage:

  Step 1  — arXiv discovery
  Step 2  — normalization + deduplication + DB persist
  Step 3  — filtering + ranking
  Step 4  — LLM evaluation skipped (recorded)
  Step 5  — PDF acquisition from arXiv
  Step 6  — PDF processing  (Milestone 8 NOT built → reported, pipeline stops here)

Usage:
  python3 scratch/end_to_end_pipeline.py
"""

import asyncio
import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ── suppress SQLAlchemy echo for cleaner output ──────────────────────────────
import os
os.environ["QUANTFORGE_ENV"] = "production"   # disables echo=True

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s  %(name)s  %(message)s",
)
# keep our own logger at INFO
log = logging.getLogger("pipeline_e2e")
log.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter("%(levelname)s  %(message)s"))
log.addHandler(handler)
log.propagate = False

DIVIDER = "─" * 65

def step(n: int, title: str) -> None:
    print(f"\n{DIVIDER}")
    print(f"  STEP {n}: {title}")
    print(DIVIDER)

def ok(msg: str)   -> None: print(f"  ✓  {msg}")
def warn(msg: str) -> None: print(f"  ⚠  {msg}")
def fail(msg: str) -> None: print(f"  ✗  {msg}")


# ─────────────────────────────────────────────────────────────────────────────
# Imports (after env var set)
# ─────────────────────────────────────────────────────────────────────────────
from quantforge.config.loader import load_pipeline_config, load_research_topics
from quantforge.database.connection import get_session
from quantforge.database.models.paper import PaperRecord
from quantforge.database.repositories.paper import PaperRepository, DuplicatePaperError
from quantforge.models.paper import ResearchPaper, SourceReference
from quantforge.models.states import PaperState
from quantforge.research.discovery.arxiv import ArxivConnector
from quantforge.research.discovery.base import SearchQuery
from quantforge.research.normalization import Deduplicator, PaperMerger, normalize_arxiv_id
from quantforge.research.filtering import CandidateRanker
from quantforge.research.acquisition.manager import AcquisitionManager
from quantforge.research.acquisition.resolver import PdfResolver
from quantforge.research.acquisition.downloader import download_pdf
from quantforge.research.acquisition.validator import validate_pdf
import httpx

CONFIG_DIR = Path(__file__).parent.parent / "config"
LIBRARY_ROOT = Path(__file__).parent.parent / "research_library"


async def run_pipeline() -> None:
    pipeline_cfg = load_pipeline_config(CONFIG_DIR)
    topics_cfg   = load_research_topics(CONFIG_DIR)
    acq_cfg      = pipeline_cfg.acquisition

    # ── STEP 1: arXiv discovery ───────────────────────────────────────────────
    step(1, "arXiv Discovery")

    topic = topics_cfg.topics[0]          # momentum
    term  = topic.search_terms[0]         # "momentum investing"
    query = SearchQuery(
        term=term,
        topic_name=topic.name,
        max_results=5,
        lookback_days=730,
    )
    ok(f"Searching arXiv for topic={topic.name!r} term={term!r}")

    async with ArxivConnector(request_delay=0) as connector:
        discovery = await connector.search(query)

    if not discovery.success:
        fail(f"Discovery failed: {discovery.errors}")
        sys.exit(1)
    if not discovery.papers:
        fail("No papers returned from arXiv")
        sys.exit(1)

    # Pick the first paper that has an arXiv ID (guaranteed to have a PDF URL)
    paper: ResearchPaper | None = None
    for p in discovery.papers:
        if p.arxiv_id:
            paper = p
            break

    if paper is None:
        fail("No paper with an arXiv ID in results")
        sys.exit(1)

    ok(f"Found {discovery.total_fetched} papers (of {discovery.total_found} total)")
    ok(f"Selected: {paper.title!r}")
    ok(f"arXiv ID : {paper.arxiv_id}")
    ok(f"Authors  : {', '.join(a.name for a in paper.authors[:3])}")
    ok(f"Date     : {paper.published_date}")
    ok(f"DOI      : {paper.doi or '—'}")

    # ── STEP 2: Normalize, dedup, persist ────────────────────────────────────
    step(2, "Normalization + Deduplication + DB Persist")

    paper.state = PaperState.DISCOVERED
    dedup = Deduplicator([])

    with get_session() as session:
        repo = PaperRepository(session)

        # Check DB for existing record
        existing_record = None
        if paper.arxiv_id:
            existing_record = repo.get_by_arxiv_id(normalize_arxiv_id(paper.arxiv_id) or paper.arxiv_id)
        if existing_record is None and paper.doi:
            existing_record = repo.get_by_doi(paper.doi)

        if existing_record:
            warn(f"Paper already in DB (id={existing_record.id}, state={existing_record.state})")
            paper_id = existing_record.id
            current_state = PaperState(existing_record.state)
        else:
            # Persist new record
            record = PaperRecord(
                id=paper.id,
                title=paper.title,
                doi=paper.doi,
                arxiv_id=paper.arxiv_id,
                abstract=paper.abstract,
                published_date=paper.published_date,
                journal=paper.journal,
                authors=[{"name": a.name, "affiliation": a.affiliation} for a in paper.authors],
                keywords=paper.keywords,
                sources=[
                    {
                        "source_name": s.source_name,
                        "source_id": s.source_id,
                        "source_url": str(s.source_url) if s.source_url else None,
                        "raw_metadata": s.raw_metadata,
                    }
                    for s in paper.sources
                ],
                state=PaperState.DISCOVERED.value,
            )
            repo.create(record)
            # Transition: DISCOVERED → NORMALIZED
            repo.transition_state(paper.id, PaperState.NORMALIZED)
            paper_id = paper.id
            current_state = PaperState.NORMALIZED
            ok(f"Persisted paper id={paper_id}")

        ok(f"DB state after Step 2: {current_state.value!r}")

    # ── STEP 3: Filtering + Ranking ──────────────────────────────────────────
    step(3, "Filtering + Ranking")

    ranker = CandidateRanker()
    candidates = ranker.rank(
        [paper],
        topic_keywords=topic.search_terms,
    )
    candidate = candidates[0]

    ok(f"Composite score : {candidate.composite_score:.4f}")
    ok(f"Relevance       : {candidate.relevance_score:.4f}")
    ok(f"Credibility     : {candidate.credibility_score:.4f}")
    ok(f"Implementability: {candidate.implementability_score:.4f}")
    ok(f"Reproducibility : {candidate.reproducibility_score:.4f}")
    ok(f"Rank #          : {candidate.rank}")

    # Persist scores + transition NORMALIZED → EVALUATED → CANDIDATE
    with get_session() as session:
        repo = PaperRepository(session)
        record = repo.get_by_id(paper_id)
        if record is None:
            fail("Paper record disappeared from DB")
            sys.exit(1)

        current_state = PaperState(record.state)

        # Scores
        repo.update_scores(
            paper_id,
            relevance=candidate.relevance_score,
            credibility=candidate.credibility_score,
            implementability=candidate.implementability_score,
            reproducibility=candidate.reproducibility_score,
            composite=candidate.composite_score,
        )

        # Drive state forward from wherever we are
        if current_state == PaperState.NORMALIZED:
            repo.transition_state(paper_id, PaperState.EVALUATED)
            current_state = PaperState.EVALUATED
        if current_state == PaperState.EVALUATED:
            repo.transition_state(paper_id, PaperState.CANDIDATE)
            current_state = PaperState.CANDIDATE

    ok(f"DB state after Step 3: {current_state.value!r}")

    # ── STEP 4: LLM Evaluation — SKIPPED ─────────────────────────────────────
    step(4, "LLM Evaluation — Skipped (not calling LLM per instructions)")

    with get_session() as session:
        repo = PaperRepository(session)
        record = repo.get_by_id(paper_id)
        existing_eval = record.evaluation or {}
        existing_eval["llm_evaluation"] = {
            "skipped": True,
            "reason": "LLM evaluation skipped per pipeline run instructions",
            "skipped_at": datetime.now(timezone.utc).isoformat(),
        }
        record.evaluation = existing_eval
        session.flush()

    ok("LLM skipped flag recorded in evaluation JSON")

    # ── STEP 5: Approve + Acquire PDF ────────────────────────────────────────
    step(5, "PDF Acquisition from arXiv")

    with get_session() as session:
        repo = PaperRepository(session)
        record = repo.get_by_id(paper_id)
        current_state = PaperState(record.state)

        # Manual-approve: CANDIDATE → APPROVED
        if current_state == PaperState.CANDIDATE:
            repo.transition_state(paper_id, PaperState.APPROVED)
            ok("Transitioned CANDIDATE → APPROVED")
            current_state = PaperState.APPROVED
        elif current_state == PaperState.APPROVED:
            ok("Paper already APPROVED")
        elif current_state == PaperState.ACQUIRED:
            ok("Paper already ACQUIRED — skipping download")
        elif current_state == PaperState.PROCESSED:
            ok("Paper already PROCESSED — skipping download and processing")
        else:
            fail(f"Unexpected state for acquisition: {current_state.value!r}")
            sys.exit(1)

    if current_state != PaperState.ACQUIRED:
        # Determine storage path
        paper_dir  = LIBRARY_ROOT / str(paper_id)
        paper_dir.mkdir(parents=True, exist_ok=True)
        arxiv_safe = (paper.arxiv_id or "paper").replace("/", "_")
        dest_path  = paper_dir / f"{arxiv_safe}.pdf"
        pdf_url    = f"https://arxiv.org/pdf/{paper.arxiv_id}"

        ok(f"Downloading: {pdf_url}")
        ok(f"Destination: {dest_path}")

        async with httpx.AsyncClient() as client:
            dl = await download_pdf(
                client=client,
                url=pdf_url,
                dest_path=dest_path,
                timeout=60.0,
                max_size_bytes=acq_cfg.max_pdf_size_mb * 1024 * 1024,
            )

        if not dl.success:
            fail(f"Download failed: {dl.failure_reason}")
            sys.exit(1)

        ok(f"Downloaded {dl.bytes_written / 1024:.1f} KB")

        # Validate
        val = validate_pdf(dest_path, acq_cfg.max_pdf_size_mb)
        if not val.valid:
            fail(f"Validation failed: {val.failure_reason}")
            dest_path.unlink(missing_ok=True)
            sys.exit(1)

        ok(f"SHA-256  : {val.sha256[:16]}…")
        ok(f"Size     : {val.size_bytes / 1024:.1f} KB")

        # Persist and transition → ACQUIRED
        with get_session() as session:
            repo = PaperRepository(session)
            record = repo.get_by_id(paper_id)
            record.file_path       = str(dest_path)
            record.file_hash       = val.sha256
            record.file_size_bytes = val.size_bytes
            existing_eval = record.evaluation or {}
            existing_eval["acquisition"] = {
                "source_url"      : pdf_url,
                "resolver_source" : "arxiv",
                "acquired_at"     : datetime.now(timezone.utc).isoformat(),
                "sha256"          : val.sha256,
                "size_bytes"      : val.size_bytes,
            }
            record.evaluation = existing_eval
            session.flush()
            repo.transition_state(paper_id, PaperState.ACQUIRED)
            current_state = PaperState.ACQUIRED

        ok(f"DB state after Step 5: {current_state.value!r}")

# ── STEP 6: PDF Processing + Research Document Package ───────────────────

    step(6, "PDF Processing + Research Document Package")

    with get_session() as session:
        repo = PaperRepository(session)
        record = repo.get_by_id(paper_id)
        current_state = PaperState(record.state)

    if current_state == PaperState.PROCESSED:
        ok("Paper already PROCESSED — skipping")
    else:
        from quantforge.research.processing.processor import PdfProcessor
        processor = PdfProcessor(storage_root=LIBRARY_ROOT)
        with get_session() as session:
            repo = PaperRepository(session)
            result = processor.process(paper_id=paper_id, repo=repo)
        if not result.success:
            fail(f"Processing failed: {result.error}")
            sys.exit(1)
        ok(f"Text extracted  : {result.extraction.char_count:,} characters")
        ok(f"Pages           : {result.extraction.page_count}")
        ok(f"Sections found  : {result.section_count}")
        ok(f"Package path    : {result.package_dir}")
        ok(f"DB state        : PROCESSED")
        

    # ── FINAL REPORT ─────────────────────────────────────────────────────────
    step(0, "FINAL REPORT")

    with get_session() as session:
        repo    = PaperRepository(session)
        record  = repo.get_by_id(paper_id)
        # Eagerly snapshot all fields before session closes
        r_title  = record.title
        r_arxiv  = record.arxiv_id
        r_id     = record.id
        r_comp   = record.composite_score
        r_rel    = record.relevance_score
        r_cred   = record.credibility_score
        r_impl   = record.implementability_score
        r_repr   = record.reproducibility_score
        r_state  = record.state
        r_fpath  = record.file_path
        r_fhash  = record.file_hash
        r_fsize  = record.file_size_bytes

    pkg_dir = LIBRARY_ROOT / str(paper_id)
    pkg_files = sorted(pkg_dir.rglob("*")) if pkg_dir.exists() else []

    print(f"\n  Paper title   : {r_title}")
    print(f"  arXiv ID      : {r_arxiv}")
    print(f"  Internal UUID : {r_id}")
    print(f"\n  Scores")
    print(f"    composite        : {r_comp:.4f}")
    print(f"    relevance        : {r_rel:.4f}")
    print(f"    credibility      : {r_cred:.4f}")
    print(f"    implementability : {r_impl:.4f}")
    print(f"    reproducibility  : {r_repr:.4f}")
    print(f"\n  DB state      : {r_state}")
    print(f"  File path     : {r_fpath}")
    print(f"  File hash     : {(r_fhash or '')[:16]}…")
    print(f"  File size     : {(r_fsize or 0) / 1024:.1f} KB")
    print(f"\n  Research Document Package ({pkg_dir}):")
    if pkg_files:
        for f in pkg_files:
            if f.is_file():
                print(f"    {f.name}  ({f.stat().st_size / 1024:.1f} KB)")
    else:
        print("    (empty)")

    print(f"\n  All 6 steps completed successfully.\n")

if __name__ == "__main__":
    asyncio.run(run_pipeline())
