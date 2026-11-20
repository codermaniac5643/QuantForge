# src/quantforge/cli/commands/strategy_extract.py
"""
quantforge strategy extract <subcommand>

Subcommands:
  single  — run extraction for one paper  (default when PAPER_ID given)
  batch   — run extraction for all papers in PROCESSED state
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from uuid import UUID

import click

from quantforge.cli.common import (
    config_dir_option,
    json_output_option,
    library_root_option,
    print_json,
)
from quantforge.database.connection import get_session
from quantforge.database.repositories.paper import PaperRepository
from quantforge.database.repositories.strategy import StrategyRepository
from quantforge.models.states import PaperState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_budget_manager(config_dir: Path):  # type: ignore[return]
    """Load a BudgetManager from llm.json."""
    from quantforge.config.loader import load_llm_config
    from quantforge.llm.budget import BudgetManager

    llm_cfg = load_llm_config(config_dir)
    return BudgetManager(config=llm_cfg)


def _make_extraction_config(config_dir: Path):  # type: ignore[return]
    """Load ExtractionConfig from strategy.json."""
    from quantforge.strategy.extraction.pipeline import ExtractionConfig

    return ExtractionConfig.from_json(config_dir / "strategy.json")


def _run_one_paper(
    paper_id: UUID,
    package_dir: Path,
    config_dir: Path,
    library_root: Path,
) -> dict:
    """
    Run the full extraction + write pipeline for a single paper.

    Returns a summary dict suitable for display or JSON output.
    """
    from quantforge.llm.budget import BudgetExhaustedError
    from quantforge.strategy.extraction.pipeline import run_extraction_pipeline
    from quantforge.strategy.writer import ProvenanceCheckError, SpecWriter

    budget = _make_budget_manager(config_dir)
    cfg = _make_extraction_config(config_dir)

    try:
        pipeline_result = asyncio.run(
            run_extraction_pipeline(
                paper_id=paper_id,
                package_dir=package_dir,
                budget_manager=budget,
                config=cfg,
            )
        )
    except BudgetExhaustedError as exc:
        return {
            "paper_id": str(paper_id),
            "outcome": "budget_exhausted",
            "error": str(exc),
        }
    except Exception as exc:
        return {
            "paper_id": str(paper_id),
            "outcome": "pipeline_error",
            "error": str(exc),
        }

    with get_session() as session:
        writer = SpecWriter(session=session, storage_root=library_root)
        try:
            if not pipeline_result.success and not pipeline_result.is_partial:
                # Extraction failed before any spec was assembled
                writer.write_draft_on_failure(
                    paper_id=paper_id,
                    error=pipeline_result.error or "unknown",
                )
                return {
                    "paper_id": str(paper_id),
                    "outcome": "extraction_failed",
                    "error": pipeline_result.error,
                }

            write_result = writer.write(pipeline_result)
        except ProvenanceCheckError as exc:
            return {
                "paper_id": str(paper_id),
                "outcome": "provenance_error",
                "error": str(exc),
            }
        except Exception as exc:
            return {
                "paper_id": str(paper_id),
                "outcome": "write_error",
                "error": str(exc),
            }

    if not write_result.success:
        return {
            "paper_id": str(paper_id),
            "outcome": "write_failed",
            "error": write_result.error,
        }

    return {
        "paper_id": str(paper_id),
        "strategy_id": str(write_result.strategy_id),
        "version": write_result.version,
        "file_path": str(write_result.file_path),
        "outcome": "partial" if pipeline_result.is_partial else "success",
        "completeness_class": (
            pipeline_result.spec.completeness_class.value
            if pipeline_result.spec
            else "unknown"
        ),
        "tokens_in": (
            pipeline_result.spec.extraction_audit.total_input_tokens
            if pipeline_result.spec and pipeline_result.spec.extraction_audit
            else 0
        ),
        "tokens_out": (
            pipeline_result.spec.extraction_audit.total_output_tokens
            if pipeline_result.spec and pipeline_result.spec.extraction_audit
            else 0
        ),
    }


def _find_package_dir(paper_record, library_root: Path) -> Path | None:
    """
    Derive the Research Document Package directory for a paper record.
    Expects: library_root/<paper_id>/
    """
    pkg_dir = library_root / str(paper_record.id)
    return pkg_dir if pkg_dir.is_dir() else None


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------


@click.group("extract")
def extract_group() -> None:
    """Run the LLM extraction pipeline on research papers."""


# ---------------------------------------------------------------------------
# extract single
# ---------------------------------------------------------------------------


@extract_group.command("single")
@click.argument("paper_id")
@config_dir_option
@library_root_option
@json_output_option
def extract_single_cmd(
    paper_id: str,
    config_dir: Path,
    library_root: Path,
    output_json: bool,
) -> None:
    """
    Run extraction for one paper identified by PAPER_ID (UUID).

    The paper must have a Research Document Package in the library.
    """
    try:
        pid = UUID(paper_id)
    except ValueError:
        click.echo(f"Error: invalid UUID {paper_id!r}", err=True)
        sys.exit(1)

    with get_session() as session:
        paper = PaperRepository(session).get_by_id(pid)

    if paper is None:
        click.echo(f"Error: paper {paper_id} not found in database.", err=True)
        sys.exit(1)

    pkg_dir = _find_package_dir(paper, library_root)
    if pkg_dir is None:
        click.echo(
            f"Error: no Research Document Package found at {library_root / paper_id}.",
            err=True,
        )
        sys.exit(1)

    if not output_json:
        click.echo(f"🔬  Extracting paper {paper_id} …")
    summary = _run_one_paper(
        paper_id=pid,
        package_dir=pkg_dir,
        config_dir=config_dir,
        library_root=library_root,
    )

    if output_json:
        print_json(summary)
        return

    outcome = summary.get("outcome", "unknown")
    icon = {"success": "✅", "partial": "⚡", "budget_exhausted": "💰"}.get(
        outcome, "❌"
    )
    click.echo(f"\n{icon}  Extraction outcome: {outcome}")
    for k, v in summary.items():
        if k != "outcome":
            click.echo(f"  {k:<24} {v}")
    click.echo()
    if outcome not in ("success", "partial"):
        sys.exit(1)


# ---------------------------------------------------------------------------
# extract batch
# ---------------------------------------------------------------------------


@extract_group.command("batch")
@config_dir_option
@library_root_option
@json_output_option
@click.option(
    "--state",
    "paper_state",
    default="processed",
    show_default=True,
    help="PaperState to extract from (processed | approved | acquired).",
)
@click.option(
    "--limit",
    default=0,
    show_default=True,
    type=int,
    help="Max papers to process (0 = no limit).",
)
@click.option(
    "--skip-existing/--no-skip-existing",
    default=True,
    show_default=True,
    help="Skip papers that already have a base spec (any status).",
)
def extract_batch_cmd(
    config_dir: Path,
    library_root: Path,
    output_json: bool,
    paper_state: str,
    limit: int,
    skip_existing: bool,
) -> None:
    """
    Run extraction for all papers in a given pipeline state.

    Default state is 'processed' (papers that have a Research Document Package).
    """
    try:
        target_state = PaperState(paper_state)
    except ValueError:
        valid = ", ".join(s.value for s in PaperState)
        click.echo(
            f"Error: unknown state {paper_state!r}. Valid: {valid}", err=True
        )
        sys.exit(1)

    with get_session() as session:
        paper_repo = PaperRepository(session)
        strategy_repo = StrategyRepository(session)

        from sqlalchemy import select
        from quantforge.database.models.paper import PaperRecord

        stmt = (
            select(PaperRecord)
            .where(PaperRecord.state == target_state.value)
            .order_by(PaperRecord.created_at.asc())
        )
        if limit > 0:
            stmt = stmt.limit(limit)
        papers = list(session.scalars(stmt))

        if skip_existing:
            existing_ids: set[UUID] = set()
            for p in papers:
                records = strategy_repo.get_by_paper_id(p.id)
                if any(r.variant == "base" for r in records):
                    existing_ids.add(p.id)
            papers = [p for p in papers if p.id not in existing_ids]

    if not papers:
        click.echo(
            f"No papers in state={paper_state!r} to extract"
            + (" (after skipping existing specs)" if skip_existing else "")
            + "."
        )
        return

    if not output_json:
        click.echo(
            f"🔬  Batch extraction: {len(papers)} paper(s) in state={paper_state!r}",
        )

    summaries: list[dict] = []
    for i, paper in enumerate(papers, 1):
        pid = paper.id
        pkg_dir = _find_package_dir(paper, library_root)
        if pkg_dir is None:
            if not output_json:
                click.echo(
                    f"  [{i}/{len(papers)}] ⚠️  Skipping {pid} — no package dir found.",
                )
            summaries.append(
                {
                    "paper_id": str(pid),
                    "outcome": "skipped",
                    "error": "package dir not found",
                }
            )
            continue

        if not output_json:
            click.echo(f"  [{i}/{len(papers)}] Processing {pid} …")
        summary = _run_one_paper(
            paper_id=pid,
            package_dir=pkg_dir,
            config_dir=config_dir,
            library_root=library_root,
        )
        summaries.append(summary)
        icon = {"success": "✅", "partial": "⚡"}.get(summary["outcome"], "❌")
        if not output_json:
            click.echo(
                f"  [{i}/{len(papers)}] {icon}  {pid}  {summary['outcome']}",
            )

    # Tally
    counts: dict[str, int] = {}
    for s in summaries:
        counts[s["outcome"]] = counts.get(s["outcome"], 0) + 1

    if output_json:
        print_json({"summaries": summaries, "totals": counts})
        return

    click.echo(f"\n📊  Batch complete — {len(summaries)} paper(s) processed")
    for outcome, count in sorted(counts.items()):
        icon = {"success": "✅", "partial": "⚡", "skipped": "⚠️"}.get(outcome, "❌")
        click.echo(f"  {icon}  {outcome:<20} {count}")
    click.echo()
    if any(k not in ("success", "partial", "skipped") for k in counts):
        sys.exit(1)
