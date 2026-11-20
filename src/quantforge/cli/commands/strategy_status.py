# src/quantforge/cli/commands/strategy_status.py
"""
quantforge strategy status

Summary of all strategy specs by status, flagged rule counts, and pending
review queue. Single command — no subgroups.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
from sqlalchemy import func, select

from quantforge.cli.common import (
    config_dir_option,
    json_output_option,
    library_root_option,
    print_json,
    print_table,
)
from quantforge.database.connection import get_session
from quantforge.database.models.strategy import StrategyRecord
from quantforge.strategy.models.enums import ProvenanceLabel, StrategyStatus


def _count_flagged_rules(file_path: str | None) -> int:
    """
    Count rules that need reviewer attention in a spec JSON file:
      - inferred rules without evidence
      - quantforge_added rules (should not exist in base specs)

    Returns 0 if the file is missing or unparseable.
    """
    if not file_path:
        return 0
    import json
    from pathlib import Path as _Path

    p = _Path(file_path)
    if not p.exists():
        return 0
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return 0

    pillars = data.get("pillars", {})
    flagged = 0
    for pillar_data in pillars.values():
        if not isinstance(pillar_data, dict):
            continue
        for rule in pillar_data.get("rules", []):
            prov = rule.get("provenance", "")
            evidence = rule.get("evidence", "")
            if prov == ProvenanceLabel.INFERRED.value and not evidence:
                flagged += 1
            if prov == ProvenanceLabel.QUANTFORGE_ADDED.value:
                flagged += 1
    return flagged


@click.command("status")
@config_dir_option
@library_root_option
@json_output_option
def strategy_status_cmd(
    config_dir: Path,
    library_root: Path,
    output_json: bool,
) -> None:
    """
    Show a summary of all strategy specs grouped by status.

    Includes:
      - Count per status (draft, under_review, approved, rejected, superseded)
      - Total flagged rules across all non-superseded, non-rejected specs
      - Number of specs pending review
    """
    with get_session() as session:
        # Counts by status
        stmt = (
            select(StrategyRecord.status, func.count().label("count"))
            .group_by(StrategyRecord.status)
        )
        rows = session.execute(stmt).all()
        counts: dict[str, int] = {row.status: row.count for row in rows}

        # Pending review
        pending = (
            session.scalar(
                select(func.count()).where(
                    StrategyRecord.status == StrategyStatus.UNDER_REVIEW.value
                )
            )
            or 0
        )

        # Flagged rules — scan non-terminal base specs
        flagged_total = 0
        active_records = list(
            session.scalars(
                select(StrategyRecord).where(
                    StrategyRecord.status.in_(
                        [
                            StrategyStatus.DRAFT.value,
                            StrategyStatus.UNDER_REVIEW.value,
                            StrategyStatus.APPROVED.value,
                        ]
                    ),
                    StrategyRecord.variant == "base",
                )
            )
        )
        for record in active_records:
            flagged_total += _count_flagged_rules(record.file_path)

    total = sum(counts.values())

    if output_json:
        print_json(
            {
                "total_specs": total,
                "by_status": counts,
                "pending_review": pending,
                "flagged_rules": flagged_total,
            }
        )
        return

    click.echo(f"\n📋  Strategy Engineering Status — {total} spec(s) in database\n")

    table_rows = [
        {
            "status": status,
            "count": str(count),
        }
        for status, count in sorted(counts.items(), key=lambda x: x[1], reverse=True)
    ]
    print_table(table_rows, ["status", "count"], max_width=20)
    click.echo()
    click.echo(f"  ⏳  Pending review      : {pending}")
    click.echo(f"  🚩  Flagged rules (base): {flagged_total}")
    click.echo()
