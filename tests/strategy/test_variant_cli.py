# tests/strategy/test_variant_cli.py
"""
CLI tests for `quantforge strategy variant` subcommands — Milestone 16.

Uses Click's CliRunner with mocked VariantService — no real DB/filesystem.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from quantforge.cli.main import cli
from quantforge.database.models.strategy import StrategyRecord
from quantforge.strategy.models.enums import (
    CompletenessClass,
    ExtractionMethod,
    ProvenanceLabel,
    StrategyStatus,
)
from quantforge.strategy.models.spec import RuleObject, StrategyVariant
from quantforge.strategy.variant_service import VariantError, VariantWriteResult


@pytest.fixture()
def runner():
    return CliRunner()


def _make_record(
    strategy_id: uuid.UUID | None = None,
    paper_id: uuid.UUID | None = None,
    variant: str = "with_exit",
    status: StrategyStatus = StrategyStatus.DRAFT,
    file_path: str | None = "/tmp/with_exit_v1.json",
) -> MagicMock:
    r = MagicMock(spec=StrategyRecord)
    r.id = strategy_id or uuid.uuid4()
    r.paper_id = paper_id or uuid.uuid4()
    r.version = 1
    r.variant = variant
    r.status = status.value
    r.completeness_class = CompletenessClass.ENTRY_FOCUSED.value
    r.extracted_by = ExtractionMethod.LLM_ASSISTED.value
    r.file_path = file_path
    r.created_at = "2024-01-01T00:00:00+00:00"
    return r


def _make_variant_obj(
    variant_name: str = "with_exit",
    base_id: uuid.UUID | None = None,
) -> StrategyVariant:
    return StrategyVariant(
        variant_id=f"{variant_name}_v1",
        based_on_strategy_id=base_id or uuid.uuid4(),
        based_on_version=1,
        description="Adds ATR stop-loss exit.",
        rationale="Base has no exit rules.",
        added_rules=[
            RuleObject(
                rule_id="QF001",
                description="ATR stop-loss",
                provenance=ProvenanceLabel.QUANTFORGE_ADDED,
                source_ref="QuantForge rule library",
            )
        ],
    )


def _patch_service(mock_gs, mock_svc_class, records=None, variant_obj=None, write_result=None):
    """Wire up common mock plumbing."""
    mock_session = MagicMock()
    mock_gs.return_value.__enter__ = MagicMock(return_value=mock_session)
    mock_gs.return_value.__exit__ = MagicMock(return_value=False)
    svc = mock_svc_class.return_value
    if records is not None:
        svc.list_variants.return_value = records
    if variant_obj is not None:
        svc.get_variant_spec.return_value = variant_obj
        svc._repo.get_by_id.return_value = _make_record()
    if write_result is not None:
        svc.create_variant.return_value = write_result
    return svc


# ---------------------------------------------------------------------------
# `quantforge strategy variant create`
# ---------------------------------------------------------------------------


class TestVariantCreateCmd:
    def test_invalid_uuid(self, runner):
        result = runner.invoke(
            cli,
            ["strategy", "variant", "create", "not-a-uuid", "my_variant",
             "--description", "d", "--rationale", "r"],
        )
        assert result.exit_code != 0

    def test_success(self, runner, tmp_path):
        sid = uuid.uuid4()
        write_result = VariantWriteResult(
            strategy_id=uuid.uuid4(),
            variant_name="with_exit",
            version=1,
            file_path=tmp_path / "with_exit_v1.json",
        )
        with patch("quantforge.cli.commands.strategy_variant.get_session") as mock_gs:
            with patch("quantforge.cli.commands.strategy_variant.VariantService") as MockSvc:
                _patch_service(mock_gs, MockSvc, write_result=write_result)
                result = runner.invoke(
                    cli,
                    [
                        "strategy", "variant", "create", str(sid), "with_exit",
                        "--description", "Adds exit rules.",
                        "--rationale", "No exit in base.",
                    ],
                )
        assert result.exit_code == 0
        assert "created" in result.output.lower()

    def test_service_error_shown(self, runner):
        sid = uuid.uuid4()
        with patch("quantforge.cli.commands.strategy_variant.get_session") as mock_gs:
            with patch("quantforge.cli.commands.strategy_variant.VariantService") as MockSvc:
                mock_gs.return_value.__enter__ = MagicMock(return_value=MagicMock())
                mock_gs.return_value.__exit__ = MagicMock(return_value=False)
                MockSvc.return_value.create_variant.side_effect = VariantError("APPROVED")
                result = runner.invoke(
                    cli,
                    [
                        "strategy", "variant", "create", str(sid), "v1",
                        "--description", "d", "--rationale", "r",
                    ],
                )
        assert result.exit_code != 0
        assert "Error" in result.output or "APPROVED" in result.output


# ---------------------------------------------------------------------------
# `quantforge strategy variant list`
# ---------------------------------------------------------------------------


class TestVariantListCmd:
    def test_invalid_uuid(self, runner):
        result = runner.invoke(cli, ["strategy", "variant", "list", "bad"])
        assert result.exit_code != 0

    def test_empty(self, runner):
        pid = uuid.uuid4()
        with patch("quantforge.cli.commands.strategy_variant.get_session") as mock_gs:
            with patch("quantforge.cli.commands.strategy_variant.VariantService") as MockSvc:
                _patch_service(mock_gs, MockSvc, records=[])
                result = runner.invoke(cli, ["strategy", "variant", "list", str(pid)])
        assert result.exit_code == 0
        assert "No variants" in result.output

    def test_shows_variants(self, runner):
        pid = uuid.uuid4()
        records = [_make_record(variant="v1"), _make_record(variant="v2")]
        with patch("quantforge.cli.commands.strategy_variant.get_session") as mock_gs:
            with patch("quantforge.cli.commands.strategy_variant.VariantService") as MockSvc:
                _patch_service(mock_gs, MockSvc, records=records)
                result = runner.invoke(cli, ["strategy", "variant", "list", str(pid)])
        assert result.exit_code == 0
        assert "2 variant" in result.output

    def test_json_output(self, runner):
        pid = uuid.uuid4()
        records = [_make_record(variant="v1")]
        with patch("quantforge.cli.commands.strategy_variant.get_session") as mock_gs:
            with patch("quantforge.cli.commands.strategy_variant.VariantService") as MockSvc:
                _patch_service(mock_gs, MockSvc, records=records)
                result = runner.invoke(
                    cli, ["strategy", "variant", "list", str(pid), "--json"]
                )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 1


# ---------------------------------------------------------------------------
# `quantforge strategy variant show`
# ---------------------------------------------------------------------------


class TestVariantShowCmd:
    def test_invalid_uuid(self, runner):
        result = runner.invoke(cli, ["strategy", "variant", "show", "bad"])
        assert result.exit_code != 0

    def test_terminal_display(self, runner):
        sid = uuid.uuid4()
        variant_obj = _make_variant_obj()
        with patch("quantforge.cli.commands.strategy_variant.get_session") as mock_gs:
            with patch("quantforge.cli.commands.strategy_variant.VariantService") as MockSvc:
                _patch_service(mock_gs, MockSvc, variant_obj=variant_obj)
                result = runner.invoke(cli, ["strategy", "variant", "show", str(sid)])
        assert result.exit_code == 0
        assert "Variant" in result.output
        assert "QF001" in result.output

    def test_json_output(self, runner):
        sid = uuid.uuid4()
        variant_obj = _make_variant_obj()
        with patch("quantforge.cli.commands.strategy_variant.get_session") as mock_gs:
            with patch("quantforge.cli.commands.strategy_variant.VariantService") as MockSvc:
                _patch_service(mock_gs, MockSvc, variant_obj=variant_obj)
                result = runner.invoke(
                    cli, ["strategy", "variant", "show", str(sid), "--json"]
                )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "variant_id" in data
        assert "added_rules" in data

    def test_service_error_shown(self, runner):
        sid = uuid.uuid4()
        with patch("quantforge.cli.commands.strategy_variant.get_session") as mock_gs:
            with patch("quantforge.cli.commands.strategy_variant.VariantService") as MockSvc:
                mock_gs.return_value.__enter__ = MagicMock(return_value=MagicMock())
                mock_gs.return_value.__exit__ = MagicMock(return_value=False)
                MockSvc.return_value.get_variant_spec.side_effect = VariantError("not found")
                result = runner.invoke(cli, ["strategy", "variant", "show", str(sid)])
        assert result.exit_code != 0
