# src/quantforge/cli/commands/acquire.py
"""
quantforge research acquire

Acquire PDFs for approved/auto-approved papers.
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
)


@click.command("acquire")
@config_dir_option
@library_root_option
@json_output_option
@click.option("--dry-run", is_flag=True, default=False, help="Resolve URLs but don't download.")
@click.option("--limit", default=10, show_default=True, help="Max papers to acquire.", type=int)
@click.option(
    "--paper-id",
    default=None,
    help="Acquire a specific paper by UUID.",
)
def acquire_cmd(
    config_dir: Path,
    library_root: Path,
    output_json: bool,
    dry_run: bool,
    limit: int,
    paper_id: str | None,
) -> None:
    """Download PDFs for approved or auto-approved papers."""
    import httpx
    from quantforge.config.loader import load_pipeline_config
    from quantforge.database.connection import get_session
    from quantforge.database.repositories.paper import PaperRepository
    from quantforge.models.states import PaperState
    from quantforge.research.acquisition.manager import AcquisitionManager

    pipeline_config = load_pipeline_config(config_dir)
    acq_config = pipeline_config.acquisition

    with get_session() as session:
        repo = PaperRepository(session)

        if paper_id:
            import uuid as _uuid
            try:
                ids = [_uuid.UUID(paper_id)]
            except ValueError:
                click.echo(f"Error: invalid UUID {paper_id!r}", err=True)
                raise SystemExit(1)
        else:
            # Collect APPROVED papers (manual mode) or CANDIDATE above threshold (auto)
            approved = repo.get_by_state(PaperState.APPROVED)
            candidates = (
                repo.get_by_state(PaperState.CANDIDATE)
                if acq_config.approval_mode == "auto"
                else []
            )
            eligible = approved + candidates
            eligible = eligible[:limit]
            ids = [r.id for r in eligible]

        if not ids:
            click.echo("No eligible papers to acquire.")
            return

        outcomes = []

        async def _run() -> None:
            async with httpx.AsyncClient() as client:
                manager = AcquisitionManager(
                    repo=repo,
                    config=acq_config,
                    storage_root=library_root,
                    client=client,
                )
                for pid in ids:
                    if dry_run:
                        # Resolve URL only — don't download
                        from quantforge.research.acquisition.resolver import PdfResolver
                        record = repo.get_by_id(pid)
                        if record:
                            from quantforge.models.paper import ResearchPaper, SourceReference
                            resolver = PdfResolver(mailto=None)
                            url = await resolver.resolve_pdf_url(record)
                            outcomes.append({
                                "paper_id": str(pid),
                                "arxiv_id": record.arxiv_id,
                                "title": (record.title or "")[:50],
                                "resolved_url": url.url if url else None,
                                "dry_run": True,
                            })
                    else:
                        outcome = await manager.acquire(paper_id=pid)
                        outcomes.append({
                            "paper_id": str(outcome.paper_id),
                            "success": outcome.success,
                            "state": outcome.final_state.value,
                            "file_path": str(outcome.file_path) if outcome.file_path else None,
                            "sha256": (outcome.sha256 or "")[:16] + "…" if outcome.sha256 else None,
                            "reason": outcome.failure_reason,
                        })

        asyncio.run(_run())

    if output_json:
        print_json(outcomes)
    else:
        verb = "Resolved (dry run)" if dry_run else "Acquired"
        click.echo(f"\n📥  {verb} {len(outcomes)} paper(s)\n")
        cols = (
            ["paper_id", "arxiv_id", "title", "resolved_url"]
            if dry_run
            else ["paper_id", "success", "state", "sha256", "reason"]
        )
        print_table(outcomes, cols)
        click.echo()

