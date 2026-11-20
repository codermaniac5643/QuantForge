# src/quantforge/cli/commands/ingest.py

"""
quantforge research ingest

Ingest a user-supplied PDF directly into the pipeline at ACQUIRED state.
Bypasses discovery, scoring, and acquisition — goes straight to processing.

Usage:
  quantforge research ingest --file /path/to/paper.pdf --title "Paper Title"
  quantforge research ingest --file paper.pdf --title "Paper Title" \\
      --source ssrn --source-id 1234567 \\
      --source-url "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1234567"
"""

from __future__ import annotations

from pathlib import Path

import click

from quantforge.cli.common import (
    config_dir_option,
    json_output_option,
    library_root_option,
    print_json,
)


@click.command("ingest")
@config_dir_option
@library_root_option
@json_output_option
@click.option(
    "--file", "pdf_file",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to the PDF file to ingest.",
)
@click.option(
    "--title",
    required=True,
    help="Paper title (required — not inferred from PDF).",
)
@click.option(
    "--abstract",
    default=None,
    help="Optional abstract text.",
)
@click.option(
    "--source",
    default="user_supplied",
    show_default=True,
    help="Source name e.g. ssrn, arxiv, journal.",
)
@click.option(
    "--source-id",
    default=None,
    help="Source-specific identifier e.g. SSRN abstract ID.",
)
@click.option(
    "--source-url",
    default=None,
    help="URL to the paper's abstract or landing page.",
)
@click.option(
    "--process",
    is_flag=True,
    default=False,
    help="Run PDF processing immediately after ingestion.",
)
def ingest_cmd(
    config_dir: Path,
    library_root: Path,
    output_json: bool,
    pdf_file: Path,
    title: str,
    abstract: str | None,
    source: str,
    source_id: str | None,
    source_url: str | None,
    process: bool,
) -> None:
    """Ingest a user-supplied PDF directly into the pipeline at ACQUIRED state."""
    from quantforge.config.loader import load_pipeline_config
    from quantforge.database.connection import get_session
    from quantforge.database.repositories.paper import PaperRepository
    from quantforge.research.ingestion.ingestor import ingest_pdf

    pipeline_cfg = load_pipeline_config(config_dir)
    max_mb = pipeline_cfg.acquisition.max_pdf_size_mb

    with get_session() as session:
        repo = PaperRepository(session)

        result = ingest_pdf(
            source_file=pdf_file,
            title=title,
            repo=repo,
            library_root=library_root,
            source_name=source,
            source_id=source_id,
            source_url=source_url,
            abstract=abstract,
            max_pdf_size_mb=max_mb,
        )

        if not result.success:
            if output_json:
                print_json({"success": False, "error": result.error})
            else:
                click.echo(f"Error: {result.error}", err=True)
            raise SystemExit(1)

        # --- Optionally run processing immediately ---
        if process:
            from quantforge.research.processing.processor import PdfProcessor
            processor = PdfProcessor(storage_root=library_root)
            proc_result = processor.process(
                paper_id=result.paper_id,
                repo=repo,
            )
            if not proc_result.success:
                if output_json:
                    print_json({
                        "success": True,
                        "ingested": True,
                        "paper_id": str(result.paper_id),
                        "file_path": str(result.file_path),
                        "processing_success": False,
                        "processing_error": proc_result.error,
                    })
                else:
                    click.echo(f"\n✓  Ingested: {result.paper_id}")
                    click.echo(f"   File:      {result.file_path}")
                    click.echo(f"✗  Processing failed: {proc_result.error}")
                return

            if output_json:
                print_json({
                    "success": True,
                    "paper_id": str(result.paper_id),
                    "file_path": str(result.file_path),
                    "processing_success": True,
                    "sections": proc_result.section_count,
                    "pages": proc_result.page_count,
                    "chars": proc_result.char_count,
                })
            else:
                click.echo(f"\n✓  Ingested and processed: {title[:60]}")
                click.echo(f"   Paper ID:  {result.paper_id}")
                click.echo(f"   File:      {result.file_path}")
                click.echo(f"   Pages:     {proc_result.page_count}")
                click.echo(f"   Sections:  {proc_result.section_count}")
                click.echo(f"   State:     PROCESSED\n")
            return

        # --- Ingestion only ---
        if output_json:
            print_json({
                "success": True,
                "paper_id": str(result.paper_id),
                "file_path": str(result.file_path),
                "state": "acquired",
                "next_step": "quantforge research process --paper-id " + str(result.paper_id),
            })
        else:
            click.echo(f"\n✓  Ingested: {title[:60]}")
            click.echo(f"   Paper ID:  {result.paper_id}")
            click.echo(f"   File:      {result.file_path}")
            click.echo(f"   State:     ACQUIRED")
            click.echo(
                f"\n   Next step: quantforge research process "
                f"--paper-id {result.paper_id}\n"
            )