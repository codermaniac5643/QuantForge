# src/quantforge/cli/common.py
"""
Shared helpers for the QuantForge CLI.

Provides:
  - Config and DB session bootstrapping (load_context).
  - Output helpers for tables and JSON.
  - Common Click option decorators.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import click

from quantforge.config.loader import (
    load_pipeline_config,
    load_research_topics,
    load_llm_config,
)
from quantforge.database.connection import get_session
from quantforge.database.repositories.paper import PaperRepository
from quantforge.research.filtering.ranker import CandidateRanker
from quantforge.research.pipeline.context import PipelineContext

# Default config directory — relative to repo root
DEFAULT_CONFIG_DIR = Path("config")
DEFAULT_LIBRARY_ROOT = Path("research_library")


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def build_context(
    config_dir: Path = DEFAULT_CONFIG_DIR,
    library_root: Path = DEFAULT_LIBRARY_ROOT,
    dry_run: bool = False,
    topic_names: list[str] | None = None,
) -> tuple[PipelineContext, Any]:
    """
    Load all configs and return (PipelineContext, session).

    The caller is responsible for closing the session.
    """
    pipeline_config = load_pipeline_config(config_dir)
    topics_config = load_research_topics(config_dir)
    llm_config = load_llm_config(config_dir)

    session = get_session().__enter__()  # type: ignore[attr-defined]
    repo = PaperRepository(session)
    ranker = CandidateRanker()

    ctx = PipelineContext(
        pipeline_config=pipeline_config,
        topics_config=topics_config,
        llm_config=llm_config,
        repo=repo,
        ranker=ranker,
        library_root=library_root,
        dry_run=dry_run,
        topic_names=topic_names or [],
    )
    return ctx, session


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def print_json(data: Any) -> None:
    """Print *data* as pretty-printed JSON to stdout."""
    click.echo(json.dumps(data, indent=2, default=str))


def print_table(rows: list[dict], columns: list[str], max_width: int = 40) -> None:
    """
    Print a simple text table.

    Args:
        rows:      List of dicts — each dict is one row.
        columns:   Column keys to display, in order.
        max_width: Maximum cell content width before truncation.
    """
    if not rows:
        click.echo("  (no results)")
        return

    # Compute column widths
    widths: dict[str, int] = {col: len(col) for col in columns}
    for row in rows:
        for col in columns:
            val = str(row.get(col, ""))
            widths[col] = min(max(widths[col], len(val)), max_width)

    sep = "  ".join("-" * widths[col] for col in columns)
    header = "  ".join(col.ljust(widths[col]) for col in columns)
    click.echo(header)
    click.echo(sep)
    for row in rows:
        line = "  ".join(
            str(row.get(col, ""))[:widths[col]].ljust(widths[col])
            for col in columns
        )
        click.echo(line)


def err(msg: str) -> None:
    """Print an error message to stderr and exit 1."""
    click.echo(f"Error: {msg}", err=True)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Shared Click options
# ---------------------------------------------------------------------------


config_dir_option = click.option(
    "--config-dir",
    default=str(DEFAULT_CONFIG_DIR),
    show_default=True,
    help="Path to the config/ directory.",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)

library_root_option = click.option(
    "--library-root",
    default=str(DEFAULT_LIBRARY_ROOT),
    show_default=True,
    help="Root directory for the research library.",
    type=click.Path(path_type=Path),
)

json_output_option = click.option(
    "--json",
    "output_json",
    is_flag=True,
    default=False,
    help="Output as JSON instead of a table.",
)

topic_option = click.option(
    "--topic",
    "topic_names",
    multiple=True,
    help="Filter to specific topic(s). Repeatable.",
)

