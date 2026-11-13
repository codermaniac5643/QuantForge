# src/quantforge/cli/commands/process.py
"""
quantforge research process

Extract text and build Research Document Packages for acquired papers.
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


@click.command("process")
@config_dir_option
@library_root_option
@json_output_option
@click.option("--limit", default=10, show_default=True, help="Max papers to process.", type=int)
@click.option("--paper-id", default=None, help="Process a specific paper by UUID.")
def process_cmd(
    config_dir: Path,
    library_root: Path,
    output_json: bool,
    limit: int,
    paper_id: str | None,
) -> None:
    """Extract text and create Research Document Packages for acquired papers."""
    from quantforge.database.connection import get_session
    from quantforge.database.repositories.paper import PaperRepository
    from quantforge.models.states import PaperState
    from quantforge.research.processing.processor import PdfProcessor

    with get_session() as session:
        repo = PaperRepository(session)
        processor = PdfProcessor(storage_root=library_root)

        if paper_id:
            try:
                ids = [UUID(paper_id)]
            except ValueError:
                click.echo(f"Error: invalid UUID {paper_id!r}", err=True)
                raise SystemExit(1)
        else:
            acquired = repo.get_by_state(PaperState.ACQUIRED)
            ids = [r.id for r in acquired[:limit]]

        if not ids:
            click.echo("No acquired papers to process.")
            return

        rows = []
        for pid in ids:
            result = processor.process(paper_id=pid, repo=repo)
            rows.append({
                "paper_id": str(result.paper_id)[:8] + "…",
                "success": "✓" if result.success else "✗",
                "state": result.final_state.value,
                "pages": result.extraction.page_count if result.extraction else "-",
                "sections": result.section_count,
                "tables": result.manifest.table_count if result.manifest else "-",
                "error": (result.error or "")[:40],
            })

    if output_json:
        print_json(rows)
    else:
        click.echo(f"\n⚙️   Processed {len(rows)} paper(s)\n")
        print_table(rows, ["paper_id", "success", "state", "pages", "sections", "tables", "error"])
        click.echo()

