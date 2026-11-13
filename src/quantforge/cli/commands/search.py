# src/quantforge/cli/commands/search.py
"""
quantforge research search

Discover new papers from configured sources for one or all topics.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import click

from quantforge.cli.common import (
    config_dir_option,
    json_output_option,
    library_root_option,
    print_json,
    print_table,
    topic_option,
    build_context,
)


@click.command("search")
@config_dir_option
@library_root_option
@topic_option
@json_output_option
@click.option("--dry-run", is_flag=True, default=False, help="Run without persisting.")
@click.option("--limit", default=0, help="Max results per term (0 = use config default).", type=int)
def search_cmd(
    config_dir: Path,
    library_root: Path,
    topic_names: tuple[str, ...],
    output_json: bool,
    dry_run: bool,
    limit: int,
) -> None:
    """Discover papers from arXiv and OpenAlex for configured research topics."""
    from quantforge.config.loader import load_pipeline_config, load_research_topics, load_llm_config
    from quantforge.database.connection import get_session
    from quantforge.database.repositories.paper import PaperRepository
    from quantforge.research.filtering.ranker import CandidateRanker
    from quantforge.research.pipeline.context import PipelineContext
    from quantforge.research.pipeline.orchestrator import ResearchOrchestrator

    pipeline_config = load_pipeline_config(config_dir)
    topics_config = load_research_topics(config_dir)
    llm_config = load_llm_config(config_dir)

    if limit > 0:
        pipeline_config.discovery.results_per_term = limit

    with get_session() as session:
        repo = PaperRepository(session)
        ctx = PipelineContext(
            pipeline_config=pipeline_config,
            topics_config=topics_config,
            llm_config=llm_config,
            repo=repo,
            ranker=CandidateRanker(),
            library_root=library_root,
            dry_run=dry_run,
            topic_names=list(topic_names),
        )

        orchestrator = ResearchOrchestrator(ctx)
        summary = asyncio.run(orchestrator.run())

    rows = []
    for paper_run in summary.paper_runs:
        rows.append({
            "paper_id": str(paper_run.paper_id)[:8] + "…",
            "arxiv_id": paper_run.arxiv_id or "-",
            "title": paper_run.title[:60],
            "state": paper_run.final_state or "-",
            "ok": "✓" if paper_run.success else "✗",
        })

    if output_json:
        print_json({
            "run_id": str(summary.run_id),
            "started_at": summary.started_at,
            "finished_at": summary.finished_at,
            "total_discovered": summary.total_discovered,
            "total_processed": summary.total_processed,
            "total_failed": summary.total_failed,
            "papers": rows,
        })
    else:
        click.echo(f"\n📡  Search complete — {summary.total_discovered} paper(s) discovered\n")
        print_table(rows, ["arxiv_id", "title", "state", "ok"])
        click.echo(
            f"\n  Discovered: {summary.total_discovered}  "
            f"Processed: {summary.total_processed}  "
            f"Failed: {summary.total_failed}"
        )

