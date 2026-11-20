# src/quantforge/cli/commands/strategy_variant.py
"""
quantforge strategy variant <subcommand>

Subcommands:
  create  — create a named variant from an approved base spec
  list    — list all variants for a paper
  show    — display a variant with added rules highlighted
"""

from __future__ import annotations

import sys
from pathlib import Path
from uuid import UUID

import click

from quantforge.cli.common import json_output_option, library_root_option, print_json, print_table
from quantforge.database.connection import get_session
from quantforge.strategy.models.enums import ProvenanceLabel
from quantforge.strategy.models.spec import RuleObject
from quantforge.strategy.variant_service import VariantError, VariantService

DEFAULT_LIBRARY_ROOT = Path("research_library")


@click.group("variant")
def variant_group() -> None:
    """Strategy variant management commands."""


@variant_group.command("create")
@click.argument("strategy_id")
@click.argument("variant_name")
@click.option("--description", required=True, help="What this variant adds.")
@click.option("--rationale", required=True, help="Why these rules were added.")
@library_root_option
def variant_create_cmd(
    strategy_id: str,
    variant_name: str,
    description: str,
    rationale: str,
    library_root: Path,
) -> None:
    """
    Create a named variant from STRATEGY_ID (must be APPROVED).

    VARIANT_NAME is a short slug, e.g. 'with_exit_rules'.
    Rules are added interactively or supplied via --rules-file (future).

    For now, creates an empty variant (no added_rules) as the scaffolding.
    Use 'quantforge strategy variant show' to inspect and edit the JSON.
    """
    try:
        sid = UUID(strategy_id)
    except ValueError:
        click.echo(f"Error: invalid UUID {strategy_id!r}", err=True)
        sys.exit(1)

    with get_session() as session:
        service = VariantService(session=session, storage_root=library_root)
        try:
            result = service.create_variant(
                base_strategy_id=sid,
                variant_name=variant_name,
                description=description,
                rationale=rationale,
                added_rules=[],  # scaffold — user edits JSON directly
            )
            session.commit()   # type: ignore[attr-defined]
        except VariantError as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)

    click.echo(f"✅  Variant {variant_name!r} created.")
    click.echo(f"    Strategy ID : {result.strategy_id}")
    click.echo(f"    Version     : {result.version}")
    click.echo(f"    File        : {result.file_path}")
    click.echo(f"\n  Edit the JSON file to add 'quantforge_added' rules,")
    click.echo(f"  then run 'quantforge strategy variant show <strategy_id>'.")


@variant_group.command("list")
@click.argument("paper_id")
@json_output_option
@library_root_option
def variant_list_cmd(
    paper_id: str,
    output_json: bool,
    library_root: Path,
) -> None:
    """List all variants for PAPER_ID."""
    try:
        pid = UUID(paper_id)
    except ValueError:
        click.echo(f"Error: invalid UUID {paper_id!r}", err=True)
        sys.exit(1)

    with get_session() as session:
        service = VariantService(session=session, storage_root=library_root)
        records = service.list_variants(pid)

    if not records:
        click.echo("No variants found for this paper.")
        return

    rows = [
        {
            "strategy_id": str(r.id),
            "variant": r.variant,
            "version": str(r.version),
            "status": r.status,
            "completeness": r.completeness_class,
            "file_path": r.file_path or "N/A",
        }
        for r in records
    ]

    if output_json:
        print_json(rows)
    else:
        click.echo(f"\n🔀  {len(records)} variant(s) for paper {paper_id}\n")
        print_table(rows, ["strategy_id", "variant", "version", "status"], max_width=38)
        click.echo()


@variant_group.command("show")
@click.argument("strategy_id")
@json_output_option
@library_root_option
def variant_show_cmd(
    strategy_id: str,
    output_json: bool,
    library_root: Path,
) -> None:
    """
    Show a variant with its added rules highlighted.

    STRATEGY_ID must be the UUID of a variant record (not a base spec).
    """
    try:
        sid = UUID(strategy_id)
    except ValueError:
        click.echo(f"Error: invalid UUID {strategy_id!r}", err=True)
        sys.exit(1)

    with get_session() as session:
        service = VariantService(session=session, storage_root=library_root)
        try:
            variant = service.get_variant_spec(sid)
            record = service._repo.get_by_id(sid)
        except VariantError as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)

    if output_json:
        print_json(variant.model_dump(mode="json"))
        return

    click.echo(f"\n🔀  Variant: {variant.variant_id}")
    click.echo(f"   Based on    : {variant.based_on_strategy_id} v{variant.based_on_version}")
    click.echo(f"   Description : {variant.description}")
    click.echo(f"   Rationale   : {variant.rationale}")
    click.echo(f"   DB record   : {record.id if record else 'N/A'}")
    click.echo(f"   Status      : {record.status if record else 'N/A'}")

    if variant.added_rules:
        click.echo(f"\n  ➕  Added rules ({len(variant.added_rules)}):\n")
        for rule in variant.added_rules:
            click.echo(f"  🔵  {rule.rule_id}  [{rule.provenance.value}]")
            click.echo(f"      {rule.description}")
            if rule.source_ref:
                click.echo(f"      source_ref : {rule.source_ref}")
            if rule.formula:
                click.echo(f"      formula    : {rule.formula}")
    else:
        click.echo("\n  (no added rules — edit the JSON file to add some)")
    click.echo()
