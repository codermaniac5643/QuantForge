# src/quantforge/cli/commands/rank.py
"""
quantforge research rank

Display ranked candidates from the database.
"""

from __future__ import annotations

from pathlib import Path

import click

from quantforge.cli.common import (
    config_dir_option,
    json_output_option,
    library_root_option,
    print_json,
    print_table,
    topic_option,
)


@click.command("rank")
@config_dir_option
@library_root_option
@topic_option
@json_output_option
@click.option("--limit", default=20, show_default=True, help="Max papers to show.", type=int)
@click.option(
    "--state",
    default="candidate",
    show_default=True,
    help="Filter by pipeline state (e.g. candidate, approved, acquired).",
)
@click.option(
    "--min-score",
    default=0.0,
    show_default=True,
    help="Minimum composite score threshold.",
    type=float,
)
def rank_cmd(
    config_dir: Path,
    library_root: Path,
    topic_names: tuple[str, ...],
    output_json: bool,
    limit: int,
    state: str,
    min_score: float,
) -> None:
    """Show ranked research candidates from the database."""
    from quantforge.config.loader import load_pipeline_config, load_research_topics
    from quantforge.database.connection import get_session
    from quantforge.database.repositories.paper import PaperRepository
    from quantforge.models.states import PaperState

    try:
        target_state = PaperState(state.lower())
    except ValueError:
        click.echo(f"Error: unknown state {state!r}. Valid: {[s.value for s in PaperState]}", err=True)
        raise SystemExit(1)

    with get_session() as session:
        repo = PaperRepository(session)
        records = repo.get_by_state(target_state)

        # Filter by min score
        if min_score > 0:
            records = [r for r in records if (r.composite_score or 0) >= min_score]

        # Sort by composite score descending
        records.sort(key=lambda r: (r.composite_score or 0), reverse=True)
        records = records[:limit]

        rows = []
        for r in records:
            rows.append({
                "arxiv_id": r.arxiv_id or "-",
                "score": f"{r.composite_score:.3f}" if r.composite_score else "-",
                "relevance": f"{r.relevance_score:.2f}" if r.relevance_score else "-",
                "credibility": f"{r.credibility_score:.2f}" if r.credibility_score else "-",
                "state": r.state if isinstance(r.state, str) else r.state.value,
                "title": (r.title or "")[:55],
            })

    if output_json:
        print_json(rows)
    else:
        click.echo(f"\n📊  Ranked {state} papers (top {limit}, min score ≥ {min_score})\n")
        print_table(rows, ["arxiv_id", "score", "relevance", "credibility", "state", "title"])
        click.echo(f"\n  {len(rows)} result(s)\n")

