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
    quantforge strategy extract single <paper_id>
    quantforge strategy extract batch
    quantforge strategy status
    quantforge strategy report <paper_id>
    quantforge strategy report --approved
    quantforge strategy review list
    quantforge strategy review show <id>
    quantforge strategy review approve <id>
    quantforge strategy review reject <id>
    quantforge strategy variant create <id>
    quantforge strategy variant list <paper_id>
    quantforge strategy variant show <id>
"""

import click

from quantforge.cli.commands.search import search_cmd
from quantforge.cli.commands.rank import rank_cmd
from quantforge.cli.commands.acquire import acquire_cmd
from quantforge.cli.commands.ingest import ingest_cmd
from quantforge.cli.commands.process import process_cmd
from quantforge.cli.commands.status import status_cmd
from quantforge.cli.commands.report import report_cmd
from quantforge.cli.commands.strategy_extract import extract_group
from quantforge.cli.commands.strategy_status import strategy_status_cmd
from quantforge.cli.commands.strategy_report import strategy_report_cmd
from quantforge.cli.commands.strategy_review import review_group
from quantforge.cli.commands.strategy_variant import variant_group


@click.group()
@click.version_option(package_name="quantforge")
def cli() -> None:
    """QuantForge — Quantitative Research Pipeline."""


@cli.group("research")
def research_group() -> None:
    """Research pipeline commands."""


@cli.group("strategy")
def strategy_group() -> None:
    """Strategy engineering commands."""


research_group.add_command(search_cmd, name="search")
research_group.add_command(rank_cmd, name="rank")
research_group.add_command(acquire_cmd, name="acquire")
research_group.add_command(ingest_cmd, name="ingest")
research_group.add_command(process_cmd, name="process")
research_group.add_command(status_cmd, name="status")
research_group.add_command(report_cmd, name="report")

strategy_group.add_command(extract_group, name="extract")
strategy_group.add_command(strategy_status_cmd, name="status")
strategy_group.add_command(strategy_report_cmd, name="report")
strategy_group.add_command(review_group, name="review")
strategy_group.add_command(variant_group, name="variant")
