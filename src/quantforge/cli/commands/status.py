# src/quantforge/cli/commands/status.py
"""
quantforge research status

Show pipeline state counts and recent paper activity.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import click

from quantforge.cli.common import (
    config_dir_option,
    json_output_option,
    library_root_option,
    print_json,
    print_table,
)
from quantforge.database.connection import get_session
from quantforge.database.repositories.paper import PaperRepository
from quantforge.models.states import PaperState


@click.command("status")
@config_dir_option
@library_root_option
@json_output_option
@click.option("--paper-id", default=None, help="Inspect a specific paper by UUID.")
def status_cmd(
    config_dir: Path,
    library_root: Path,
    output_json: bool,
    paper_id: str | None,
) -> None:
    """Show pipeline status — state counts or detail for one paper."""
    with get_session() as session:
        repo = PaperRepository(session)

        # --- Single paper detail ---
        if paper_id:
            try:
                pid = UUID(paper_id)
            except ValueError:
                click.echo(f"Error: invalid UUID {paper_id!r}", err=True)
                raise SystemExit(1)

            record = repo.get_by_id(pid)
            if record is None:
                click.echo(f"Paper {paper_id} not found.")
                return

            detail = {
                "id": str(record.id),
                "title": record.title,
                "arxiv_id": record.arxiv_id,
                "doi": record.doi,
                "state": record.state if isinstance(record.state, str) else record.state.value,
                "composite_score": record.composite_score,
                "relevance_score": record.relevance_score,
                "credibility_score": record.credibility_score,
                "implementability_score": record.implementability_score,
                "reproducibility_score": record.reproducibility_score,
                "file_path": record.file_path,
                "file_hash": record.file_hash,
                "file_size_bytes": record.file_size_bytes,
                "created_at": str(record.created_at),
                "updated_at": str(record.updated_at),
            }
            if output_json:
                print_json(detail)
            else:
                click.echo(f"\n📄  Paper detail\n")
                for k, v in detail.items():
                    click.echo(f"  {k:<28} {v}")
                click.echo()
            return

        # --- Overall state counts ---
        counts = repo.count_by_state()
        total = sum(counts.values())

        rows = [
            {"state": state, "count": str(count)}
            for state, count in sorted(counts.items(), key=lambda x: x[1], reverse=True)
        ]

    if output_json:
        print_json({"total": total, "by_state": counts})
    else:
        click.echo(f"\n📊  Pipeline status — {total} paper(s) in database\n")
        print_table(rows, ["state", "count"], max_width=20)
        click.echo()
