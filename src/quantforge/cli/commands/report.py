# src/quantforge/cli/commands/report.py
"""
quantforge research report

Generate a research shortlist report.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from quantforge.cli.common import (
    config_dir_option,
    json_output_option,
    library_root_option,
    print_json,
)


@click.command("report")
@config_dir_option
@library_root_option
@json_output_option
@click.option("--limit", default=20, show_default=True, help="Max papers in report.", type=int)
@click.option(
    "--min-score",
    default=0.0,
    show_default=True,
    help="Minimum composite score to include.",
    type=float,
)
@click.option(
    "--output-file",
    default=None,
    help="Write report to this file (JSON or Markdown based on extension).",
    type=click.Path(path_type=Path),
)
def report_cmd(
    config_dir: Path,
    library_root: Path,
    output_json: bool,
    limit: int,
    min_score: float,
    output_file: Path | None,
) -> None:
    """Generate a ranked research shortlist report."""
    from datetime import datetime, timezone
    from quantforge.database.connection import get_session
    from quantforge.database.repositories.paper import PaperRepository
    from quantforge.models.states import PaperState

    with get_session() as session:
        repo = PaperRepository(session)

        # Collect all non-rejected, non-discovered papers
        all_records = []
        for state in [
            PaperState.CANDIDATE, PaperState.APPROVED,
            PaperState.ACQUIRED, PaperState.PROCESSED,
        ]:
            all_records.extend(repo.get_by_state(state))

        # Filter and sort
        filtered = [r for r in all_records if (r.composite_score or 0) >= min_score]
        filtered.sort(key=lambda r: (r.composite_score or 0), reverse=True)
        filtered = filtered[:limit]

        entries = []
        for rank, r in enumerate(filtered, 1):
            entries.append({
                "rank": rank,
                "title": r.title,
                "arxiv_id": r.arxiv_id,
                "doi": r.doi,
                "state": r.state if isinstance(r.state, str) else r.state.value,
                "composite_score": round(r.composite_score or 0, 4),
                "relevance_score": round(r.relevance_score or 0, 4),
                "credibility_score": round(r.credibility_score or 0, 4),
                "implementability_score": round(r.implementability_score or 0, 4),
                "reproducibility_score": round(r.reproducibility_score or 0, 4),
                "authors": r.authors or [],
                "file_path": r.file_path,
                "file_hash": r.file_hash,
            })

    report_data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_papers": len(entries),
        "min_score_filter": min_score,
        "papers": entries,
    }

    # --- Write to file ---
    if output_file is not None:
        suffix = output_file.suffix.lower()
        if suffix == ".md":
            _write_markdown_report(report_data, output_file)
            click.echo(f"Report written to {output_file}")
        else:
            output_file.write_text(
                json.dumps(report_data, indent=2, default=str), encoding="utf-8"
            )
            click.echo(f"Report written to {output_file}")
        return

    # --- Terminal output ---
    if output_json:
        print_json(report_data)
    else:
        _print_text_report(report_data)


def _print_text_report(data: dict) -> None:
    click.echo(f"\n📋  Research Shortlist Report")
    click.echo(f"    Generated: {data['generated_at']}")
    click.echo(f"    Papers:    {data['total_papers']}\n")
    click.echo(f"  {'#':<4} {'Score':<7} {'State':<12} {'arXiv':<14} Title")
    click.echo(f"  {'-'*4} {'-'*7} {'-'*12} {'-'*14} {'-'*50}")
    for p in data["papers"]:
        click.echo(
            f"  {p['rank']:<4} "
            f"{p['composite_score']:<7.4f} "
            f"{p['state']:<12} "
            f"{(p['arxiv_id'] or '-'):<14} "
            f"{p['title'][:50]}"
        )
    click.echo()


def _write_markdown_report(data: dict, path: Path) -> None:
    lines = [
        "# QuantForge Research Shortlist Report",
        "",
        f"**Generated:** {data['generated_at']}  ",
        f"**Papers:** {data['total_papers']}  ",
        "",
        "| # | Score | State | arXiv ID | Title |",
        "|---|-------|-------|----------|-------|",
    ]
    for p in data["papers"]:
        arxiv = p["arxiv_id"] or "-"
        url = f"[{arxiv}](https://arxiv.org/abs/{arxiv})" if p["arxiv_id"] else "-"
        lines.append(
            f"| {p['rank']} "
            f"| {p['composite_score']:.4f} "
            f"| {p['state']} "
            f"| {url} "
            f"| {p['title']} |"
        )

    lines += ["", "## Paper Details", ""]
    for p in data["papers"]:
        authors = ", ".join(
            (a.get("name") or str(a)) for a in (p["authors"] or [])
        )
        lines += [
            f"### {p['rank']}. {p['title']}",
            "",
            f"- **arXiv:** {p['arxiv_id'] or 'N/A'}",
            f"- **DOI:** {p['doi'] or 'N/A'}",
            f"- **State:** {p['state']}",
            f"- **Composite score:** {p['composite_score']:.4f}",
            f"- **Relevance:** {p['relevance_score']:.4f}  "
            f"**Credibility:** {p['credibility_score']:.4f}  "
            f"**Implementability:** {p['implementability_score']:.4f}  "
            f"**Reproducibility:** {p['reproducibility_score']:.4f}",
            f"- **Authors:** {authors or 'N/A'}",
            f"- **File:** {p['file_path'] or 'not acquired'}",
            "",
        ]

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")

