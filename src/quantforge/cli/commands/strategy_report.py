# src/quantforge/cli/commands/strategy_report.py
"""
quantforge strategy report

Human-readable strategy spec report for one paper or all approved base specs.

Usage:
  quantforge strategy report <paper_id>   — full spec report for one paper
  quantforge strategy report --approved   — report all approved base specs
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import click
from sqlalchemy import select

from quantforge.cli.common import (
    config_dir_option,
    json_output_option,
    library_root_option,
    print_json,
)
from quantforge.database.connection import get_session
from quantforge.database.models.strategy import StrategyRecord
from quantforge.database.repositories.strategy import StrategyRepository
from quantforge.strategy.models.enums import StrategyStatus

ALL_PILLARS = [
    "market_regime",
    "market_eligibility",
    "asset_selection",
    "entry_signal",
    "position_sizing",
    "risk_management",
    "exit",
    "portfolio",
    "backtesting",
]

COVERAGE_ICON = {"present": "✅", "partial": "⚡", "absent": "❌"}
PROVENANCE_ICON = {
    "explicit": "📗",
    "inferred": "📙",
    "quantforge_added": "🔵",
}


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------


def _render_spec_markdown(record: StrategyRecord, spec_data: dict) -> str:
    """Render a Markdown report from a raw spec JSON dict + StrategyRecord."""
    lines: list[str] = []

    title = spec_data.get("paper_title") or f"Paper {record.paper_id}"
    lines += [
        f"# Strategy Specification Report",
        f"",
        f"**Paper:** {title}  ",
        f"**Paper ID:** `{record.paper_id}`  ",
        f"**Strategy ID:** `{record.id}`  ",
        f"**Version:** {record.version}  ",
        f"**Variant:** `{record.variant}`  ",
        f"**Status:** `{record.status}`  ",
        f"**Completeness class:** `{record.completeness_class}`  ",
        f"**Extracted by:** `{record.extracted_by}`  ",
        f"**File:** `{record.file_path or 'N/A'}`  ",
        f"**Generated:** {datetime.now(timezone.utc).isoformat()}  ",
        f"",
    ]

    # Notes
    if spec_data.get("notes"):
        lines += [f"> **Notes:** {spec_data['notes']}", f""]

    # Coverage table
    cov = spec_data.get("component_coverage", {})
    lines += [f"## Component Coverage", f"", f"| Pillar | Coverage |", f"|--------|----------|"]
    for pillar in ALL_PILLARS:
        val = cov.get(pillar, "absent")
        icon = COVERAGE_ICON.get(val, val)
        lines.append(f"| `{pillar}` | {icon} `{val}` |")
    lines.append(f"")

    # Missing components
    missing = spec_data.get("missing_components", [])
    if missing:
        lines += [f"## Missing Components", f""]
        for m in missing:
            lines.append(
                f"- **{m.get('pillar', '?')}** — {m.get('note', '')} "
                f"*(impact: {m.get('impact', 'unknown')}, action: {m.get('action', 'unknown')})*"
            )
        lines.append(f"")

    # Methodology assessment
    assess = spec_data.get("methodology_assessment", {})
    if assess:
        lines += [
            f"## Methodology Assessment",
            f"",
            f"| Field | Value |",
            f"|-------|-------|",
            f"| Implementability | `{assess.get('implementability', 'N/A')}` |",
            f"| Suitable for backtest | `{assess.get('suitable_for_backtest', 'N/A')}` |",
            f"| Primary strength | {assess.get('primary_strength', 'N/A')} |",
            f"| Primary weakness | {assess.get('primary_weakness', 'N/A')} |",
            f"",
        ]
        km = assess.get("key_missing_components", [])
        if km:
            lines.append(f"**Key missing components:** {', '.join(km)}  ")
            lines.append(f"")

    # Rules per pillar
    lines += [f"## Extracted Rules", f""]
    pillars = spec_data.get("pillars", {})
    for pillar in ALL_PILLARS:
        pillar_data = pillars.get(pillar, {})
        present = pillar_data.get("present", False)
        present_str = {True: "present", False: "absent"}.get(present, str(present))
        lines.append(
            f"### {pillar.replace('_', ' ').title()} ({present_str})"
        )
        if pillar_data.get("source_note"):
            lines.append(f"*Source:* {pillar_data['source_note']}  ")
        if pillar_data.get("missing_note"):
            lines.append(f"*Missing note:* {pillar_data['missing_note']}  ")
        lines.append(f"")

        rules = pillar_data.get("rules", [])
        if not rules:
            lines += [f"*(no rules extracted)*  ", f""]
            continue

        for rule in rules:
            prov = rule.get("provenance", "")
            icon = PROVENANCE_ICON.get(prov, "")
            lines.append(f"**{rule.get('rule_id', '?')}** {icon} `{prov}`")
            lines.append(f"> {rule.get('description', '')}")
            if rule.get("formula"):
                lines.append(f"> *Formula:* `{rule['formula']}`")
            if rule.get("parameters"):
                for k, v in rule["parameters"].items():
                    lines.append(f"> *{k}:* {v}")
            if rule.get("source_ref"):
                lines.append(f"> *Source ref:* {rule['source_ref']}")
            if rule.get("evidence"):
                lines.append(f"> *Evidence:* {rule['evidence']}")
            if rule.get("uncertainty_note"):
                lines.append(f"> ⚠️ *Uncertainty:* {rule['uncertainty_note']}")
            lines.append(f"")
        lines.append(f"")

    # Extraction audit
    audit = spec_data.get("extraction_audit", {})
    if audit:
        lines += [
            f"## Extraction Audit",
            f"",
            f"| Field | Value |",
            f"|-------|-------|",
            f"| Extracted at | {audit.get('extracted_at', 'N/A')} |",
            f"| Sections used | {len(audit.get('sections_used', []))} |",
            f"| Sections skipped | {len(audit.get('sections_skipped', []))} |",
            f"| Sections filtered (too short) | {len(audit.get('sections_filtered_too_short', []))} |",
            f"| Total input tokens | {audit.get('total_input_tokens', 0)} |",
            f"| Total output tokens | {audit.get('total_output_tokens', 0)} |",
            f"",
        ]

    return "\n".join(lines)


def _load_spec_json(file_path: str | None) -> dict | None:
    """Load a spec JSON file. Returns None on failure."""
    if not file_path:
        return None
    p = Path(file_path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _report_for_record(record: StrategyRecord) -> dict:
    """Build a report dict for one StrategyRecord."""
    spec = _load_spec_json(record.file_path)
    return {
        "strategy_id": str(record.id),
        "paper_id": str(record.paper_id),
        "version": record.version,
        "variant": record.variant,
        "status": record.status,
        "completeness_class": record.completeness_class,
        "file_path": record.file_path,
        "spec": spec,
        "load_error": None if spec is not None else "file missing or unreadable",
    }


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@click.command("report")
@click.argument("paper_id", required=False, default=None)
@config_dir_option
@library_root_option
@json_output_option
@click.option(
    "--approved",
    "approved_only",
    is_flag=True,
    default=False,
    help="Report all approved base specs (ignores PAPER_ID).",
)
@click.option(
    "--output-file",
    default=None,
    help="Write the report to this file (.md or .json).",
    type=click.Path(path_type=Path),
)
def strategy_report_cmd(
    paper_id: str | None,
    config_dir: Path,
    library_root: Path,
    output_json: bool,
    approved_only: bool,
    output_file: Path | None,
) -> None:
    """
    Generate a human-readable strategy spec report.

    PAPER_ID (UUID): report on the latest base spec for this paper.
    --approved:      report all approved base specs.

    Use --output-file to write Markdown to disk.
    """
    if not paper_id and not approved_only:
        click.echo(
            "Error: provide PAPER_ID or use --approved to report all approved specs.",
            err=True,
        )
        sys.exit(1)

    with get_session() as session:
        repo = StrategyRepository(session)

        if approved_only:
            records = list(
                session.scalars(
                    select(StrategyRecord).where(
                        StrategyRecord.status == StrategyStatus.APPROVED.value,
                        StrategyRecord.variant == "base",
                    ).order_by(StrategyRecord.created_at.asc())
                )
            )
        else:
            try:
                pid = UUID(paper_id)  # type: ignore[arg-type]
            except (ValueError, TypeError):
                click.echo(f"Error: invalid UUID {paper_id!r}", err=True)
                sys.exit(1)

            # Latest non-superseded/non-rejected base spec for this paper
            record = repo.get_latest_base(pid)
            if record is None:
                click.echo(
                    f"No base spec found for paper {paper_id} "
                    f"(try `quantforge strategy extract single {paper_id}`).",
                    err=True,
                )
                sys.exit(1)
            records = [record]

    if not records:
        click.echo("No approved base specs found.")
        return

    reports = [_report_for_record(r) for r in records]

    # --- JSON output ---
    if output_json:
        payload = reports[0] if len(reports) == 1 else reports
        if output_file:
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_text(
                json.dumps(payload, indent=2, default=str), encoding="utf-8"
            )
            click.echo(f"✅  JSON report written to {output_file}")
        else:
            print_json(payload)
        return

    # --- Markdown output ---
    md_parts: list[str] = []
    for rep in reports:
        record_obj = next(r for r in records if str(r.id) == rep["strategy_id"])
        if rep["spec"] is None:
            md_parts.append(
                f"# Report for paper {rep['paper_id']}\n\n"
                f"> ⚠️ Spec file missing or unreadable: `{rep['file_path']}`\n"
            )
        else:
            md_parts.append(_render_spec_markdown(record_obj, rep["spec"]))

    markdown = "\n\n---\n\n".join(md_parts)

    if output_file:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(markdown, encoding="utf-8")
        click.echo(f"✅  Report written to {output_file}")
    else:
        click.echo(markdown)
