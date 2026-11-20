# src/quantforge/cli/commands/strategy_review.py
"""
quantforge strategy review <subcommand>

Subcommands:
  list     — show all specs pending review
  show     — display a spec with checklist and flagged items
  approve  — approve after checklist confirmation
  reject   — reject with required notes
"""

from __future__ import annotations

import sys
from pathlib import Path
from uuid import UUID

import click

from quantforge.cli.common import json_output_option, print_json, print_table
from quantforge.database.connection import get_session
from quantforge.strategy.models.enums import StrategyStatus
from quantforge.strategy.review import ReviewService, ReviewSummary


# ---------------------------------------------------------------------------
# Markdown sheet renderer
# ---------------------------------------------------------------------------


def _render_markdown(summary: ReviewSummary) -> str:
    """Render a full Markdown review sheet from a ReviewSummary."""
    record = summary.record
    spec = summary.spec
    lines: list[str] = []

    lines.append(f"# Strategy Review Sheet")
    lines.append(f"")
    lines.append(f"**Strategy ID:** `{record.id}`  ")
    lines.append(f"**Paper ID:** `{record.paper_id}`  ")
    lines.append(f"**Version:** {record.version}  ")
    lines.append(f"**Variant:** `{record.variant}`  ")
    lines.append(f"**Status:** `{record.status}`  ")
    lines.append(f"**Extracted by:** `{record.extracted_by}`  ")
    lines.append(f"**File:** `{record.file_path or 'N/A'}`  ")
    lines.append(f"")

    if summary.load_error:
        lines.append(f"> ⚠️ **Load error:** {summary.load_error}")
        lines.append(f"")

    # --- Checklist ---
    lines.append(f"## Review Checklist")
    lines.append(f"")
    for item in summary.checklist.items:
        icon = "✅" if item.passed else "❌"
        lines.append(f"{icon} **{item.number}.** {item.description}")
        if item.detail:
            lines.append(f"   > {item.detail}")
    lines.append(f"")

    overall = "✅ ALL CHECKS PASSED" if summary.checklist.passed else "❌ CHECKLIST FAILED"
    lines.append(f"**Overall:** {overall}")
    lines.append(f"")

    if spec is None:
        return "\n".join(lines)

    # --- Completeness ---
    lines.append(f"## Completeness")
    lines.append(f"")
    lines.append(f"**Class:** `{spec.completeness_class.value}`  ")
    if spec.notes:
        lines.append(f"**Notes:** {spec.notes}  ")
    lines.append(f"")

    # Coverage table
    lines.append(f"| Pillar | Coverage |")
    lines.append(f"|--------|----------|")
    cov = spec.component_coverage
    for pillar in [
        "market_regime", "market_eligibility", "asset_selection",
        "entry_signal", "position_sizing", "risk_management",
        "exit", "portfolio", "backtesting",
    ]:
        cov_val = getattr(cov, pillar).value
        icon = {"present": "✅", "partial": "⚡", "absent": "❌"}.get(cov_val, cov_val)
        lines.append(f"| `{pillar}` | {icon} `{cov_val}` |")
    lines.append(f"")

    # --- Pillars and rules ---
    lines.append(f"## Extracted Rules")
    lines.append(f"")

    pillar_map = {
        "market_regime": spec.pillars.market_regime,
        "market_eligibility": spec.pillars.market_eligibility,
        "asset_selection": spec.pillars.asset_selection,
        "entry_signal": spec.pillars.entry_signal,
        "position_sizing": spec.pillars.position_sizing,
        "risk_management": spec.pillars.risk_management,
        "exit": spec.pillars.exit,
        "portfolio": spec.pillars.portfolio,
        "backtesting": spec.pillars.backtesting,
    }

    for pillar_name, pillar_data in pillar_map.items():
        present_str = {True: "present", False: "absent", "partial": "partial"}.get(
            pillar_data.present, str(pillar_data.present)
        )
        lines.append(f"### {pillar_name.replace('_', ' ').title()} ({present_str})")
        if pillar_data.source_note:
            lines.append(f"*Source:* {pillar_data.source_note}  ")
        if pillar_data.missing_note:
            lines.append(f"*Missing note:* {pillar_data.missing_note}  ")
        lines.append(f"")

        if not pillar_data.rules:
            lines.append(f"*(no rules extracted)*  ")
            lines.append(f"")
            continue

        for rule in pillar_data.rules:
            prov = rule.provenance.value
            prov_icon = {
                "explicit": "📗",
                "inferred": "📙",
                "quantforge_added": "🔵",
            }.get(prov, "")
            lines.append(f"**{rule.rule_id}** {prov_icon} `{prov}`")
            lines.append(f"> {rule.description}")
            if rule.source_ref:
                lines.append(f"> *Source ref:* {rule.source_ref}  ")
            if rule.formula:
                lines.append(f"> *Formula:* `{rule.formula}`  ")
            if rule.timeframe:
                lines.append(f"> *Timeframe:* {rule.timeframe}  ")
            if rule.data_inputs:
                lines.append(f"> *Data inputs:* {rule.data_inputs}  ")
            if rule.evidence:
                lines.append(f"> *Evidence:* {rule.evidence}  ")
            elif rule.provenance.value == "inferred":
                lines.append(f"> ⚠️ **MISSING EVIDENCE**  ")
            if rule.notes:
                lines.append(f"> *Notes:* {rule.notes}  ")
            lines.append(f"")

    # --- Missing components ---
    if spec.missing_components:
        lines.append(f"## Missing Components")
        lines.append(f"")
        lines.append(f"| Pillar | Status | Impact | Recommended Action |")
        lines.append(f"|--------|--------|--------|--------------------|")
        for mc in spec.missing_components:
            lines.append(
                f"| `{mc.pillar}` | {mc.status} | {mc.impact.value} | {mc.recommended_action.value} |"
            )
        lines.append(f"")

    # --- Methodology assessment ---
    if spec.methodology_assessment:
        ma = spec.methodology_assessment
        lines.append(f"## Methodology Assessment")
        lines.append(f"")
        lines.append(f"**Implementability:** `{ma.implementability.value}`  ")
        lines.append(f"**Suitable for backtest:** {ma.suitable_for_backtest}  ")
        lines.append(f"**Strength:** {ma.primary_strength}  ")
        lines.append(f"**Weakness:** {ma.primary_weakness}  ")
        if ma.key_missing_components:
            lines.append(f"**Key missing:** {', '.join(ma.key_missing_components)}  ")
        lines.append(f"**Reason:** {ma.reason}  ")
        lines.append(f"")

    # --- Extraction audit ---
    if spec.extraction_audit:
        audit = spec.extraction_audit
        lines.append(f"## Extraction Audit")
        lines.append(f"")
        lines.append(f"**Date:** {audit.extraction_date}  ")
        lines.append(f"**Input tokens:** {audit.total_input_tokens}  ")
        lines.append(f"**Output tokens:** {audit.total_output_tokens}  ")
        lines.append(f"**Sections used:** {audit.sections_used}  ")
        lines.append(f"**Sections skipped:** {audit.sections_skipped}  ")
        lines.append(f"**Sections filtered (too short):** {audit.sections_filtered_too_short}  ")
        lines.append(f"")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI group and subcommands
# ---------------------------------------------------------------------------


@click.group("review")
def review_group() -> None:
    """Strategy review workflow commands."""


@review_group.command("list")
@json_output_option
def review_list_cmd(output_json: bool) -> None:
    """List all strategy specs pending review (status=under_review)."""
    with get_session() as session:
        service = ReviewService(session)
        records = service.list_pending_review()

    if not records:
        click.echo("No specs pending review.")
        return

    rows = [
        {
            "strategy_id": str(r.id),
            "paper_id": str(r.paper_id),
            "version": str(r.version),
            "variant": r.variant,
            "completeness": r.completeness_class,
            "extracted_by": r.extracted_by,
            "created_at": str(r.created_at)[:19],
        }
        for r in records
    ]

    if output_json:
        print_json(rows)
    else:
        click.echo(f"\n📋  {len(records)} spec(s) pending review\n")
        print_table(
            rows,
            ["strategy_id", "paper_id", "version", "variant", "completeness"],
            max_width=38,
        )
        click.echo()


@review_group.command("show")
@click.argument("strategy_id")
@json_output_option
@click.option(
    "--output-file",
    default=None,
    type=click.Path(path_type=Path),
    help="Write a formatted Markdown review sheet to this file.",
)
def review_show_cmd(
    strategy_id: str,
    output_json: bool,
    output_file: Path | None,
) -> None:
    """Show a strategy spec with checklist results and flagged items."""
    try:
        sid = UUID(strategy_id)
    except ValueError:
        click.echo(f"Error: invalid UUID {strategy_id!r}", err=True)
        sys.exit(1)

    with get_session() as session:
        service = ReviewService(session)
        try:
            summary = service.get_review_summary(sid)
        except ValueError as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)

    if output_file:
        md = _render_markdown(summary)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(md, encoding="utf-8")
        click.echo(f"✅  Review sheet written to {output_file}")
        return

    if output_json:
        data = {
            "strategy_id": str(summary.record.id),
            "paper_id": str(summary.record.paper_id),
            "version": summary.record.version,
            "variant": summary.record.variant,
            "status": summary.record.status,
            "completeness_class": summary.record.completeness_class,
            "file_path": summary.record.file_path,
            "load_error": summary.load_error,
            "checklist": [
                {
                    "number": item.number,
                    "description": item.description,
                    "passed": item.passed,
                    "detail": item.detail,
                }
                for item in summary.checklist.items
            ],
            "checklist_passed": summary.checklist.passed,
        }
        print_json(data)
        return

    # Terminal display
    r = summary.record
    click.echo(f"\n📄  Strategy Spec\n")
    click.echo(f"  ID            {r.id}")
    click.echo(f"  Paper         {r.paper_id}")
    click.echo(f"  Version       {r.version}  variant={r.variant}")
    click.echo(f"  Status        {r.status}")
    click.echo(f"  Completeness  {r.completeness_class}")
    click.echo(f"  Extracted by  {r.extracted_by}")
    click.echo(f"  File          {r.file_path or 'N/A'}")
    if summary.load_error:
        click.echo(f"\n  ⚠️  {summary.load_error}")
    click.echo(f"\n📋  Review Checklist\n")
    for item in summary.checklist.items:
        icon = "✅" if item.passed else "❌"
        click.echo(f"  {icon}  {item.number}. {item.description}")
        if item.detail:
            click.echo(f"       {item.detail}")
    overall = "✅  All checks passed" if summary.checklist.passed else "❌  Checklist FAILED"
    click.echo(f"\n  {overall}\n")
    click.echo("  Tip: use --output-file <path.md> for a full Markdown review sheet.\n")


@review_group.command("approve")
@click.argument("strategy_id")
@click.option("--reviewer", required=True, help="Your reviewer identifier.")
@click.option("--yes", "confirmed", is_flag=True, default=False,
              help="Skip the interactive confirmation prompt.")
def review_approve_cmd(
    strategy_id: str,
    reviewer: str,
    confirmed: bool,
) -> None:
    """Approve a strategy spec after the checklist passes."""
    try:
        sid = UUID(strategy_id)
    except ValueError:
        click.echo(f"Error: invalid UUID {strategy_id!r}", err=True)
        sys.exit(1)

    with get_session() as session:
        service = ReviewService(session)
        try:
            summary = service.get_review_summary(sid)
        except ValueError as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)

        if not summary.checklist.passed:
            click.echo("\n❌  Cannot approve — checklist failures:\n")
            for f in summary.checklist.failures:
                click.echo(f"  [{f.number}] {f.description}")
                if f.detail:
                    click.echo(f"       {f.detail}")
            click.echo()
            sys.exit(1)

        if not confirmed:
            click.echo(f"\n✅  All checklist items pass for spec {sid}.")
            confirmed = click.confirm("Approve this spec?", default=False)
            if not confirmed:
                click.echo("Approval cancelled.")
                return

        try:
            service.approve_spec(sid, reviewer_id=reviewer)
            session.commit()  # type: ignore[attr-defined]
        except (ValueError, Exception) as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)

    click.echo(f"✅  Spec {sid} approved by {reviewer!r}.")


@review_group.command("reject")
@click.argument("strategy_id")
@click.option("--reviewer", required=True, help="Your reviewer identifier.")
@click.option("--notes", required=True, help="Notes explaining what needs to be corrected.")
def review_reject_cmd(
    strategy_id: str,
    reviewer: str,
    notes: str,
) -> None:
    """Reject a strategy spec with required notes."""
    try:
        sid = UUID(strategy_id)
    except ValueError:
        click.echo(f"Error: invalid UUID {strategy_id!r}", err=True)
        sys.exit(1)

    with get_session() as session:
        service = ReviewService(session)
        try:
            service.reject_spec(sid, reviewer_id=reviewer, notes=notes)
            session.commit()  # type: ignore[attr-defined]
        except (ValueError, Exception) as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)

    click.echo(f"❌  Spec {sid} rejected by {reviewer!r}.")
    click.echo(f"    Notes: {notes}")
