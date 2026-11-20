# src/quantforge/cli/main.py
"""
QuantForge CLI entry point.

Usage:
    quantforge research search
    quantforge research rank
    quantforge research acquire
    quantforge research ingest
    quantforge research process
    quantforge research status
    quantforge research report
"""

import click

from quantforge.cli.commands.search import search_cmd
from quantforge.cli.commands.rank import rank_cmd
from quantforge.cli.commands.acquire import acquire_cmd
from quantforge.cli.commands.ingest import ingest_cmd
from quantforge.cli.commands.process import process_cmd
from quantforge.cli.commands.status import status_cmd
from quantforge.cli.commands.report import report_cmd


@click.group()
@click.version_option(package_name="quantforge")
def cli() -> None:
    """QuantForge — Quantitative Research Pipeline."""


@cli.group("research")
def research_group() -> None:
    """Research pipeline commands."""


research_group.add_command(search_cmd, name="search")
research_group.add_command(rank_cmd, name="rank")
research_group.add_command(acquire_cmd, name="acquire")
research_group.add_command(ingest_cmd, name="ingest")
research_group.add_command(process_cmd, name="process")
research_group.add_command(status_cmd, name="status")
research_group.add_command(report_cmd, name="report")
