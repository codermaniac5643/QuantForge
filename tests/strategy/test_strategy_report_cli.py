# tests/strategy/test_strategy_report_cli.py
"""
Tests for `quantforge strategy report`.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from quantforge.cli.main import cli

PAPER_ID = str(uuid.uuid4())
STRATEGY_ID = str(uuid.uuid4())


def _make_runner() -> CliRunner:
    return CliRunner()


def _make_strategy_record(
    sid: str = STRATEGY_ID,
    pid: str = PAPER_ID,
    file_path: str | None = None,
    status: str = "approved",
) -> MagicMock:
    r = MagicMock()
    r.id = uuid.UUID(sid)
    r.paper_id = uuid.UUID(pid)
    r.version = 1
    r.variant = "base"
    r.status = status
    r.completeness_class = "full"
    r.extracted_by = "llm_assisted"
    r.file_path = file_path
    return r


MINIMAL_SPEC = {
    "paper_title": "Test Paper",
    "pillars": {
        "market_regime": {"present": False, "rules": []},
        "market_eligibility": {"present": False, "rules": []},
        "asset_selection": {"present": False, "rules": []},
        "entry_signal": {
            "present": True,
            "rules": [
                {
                    "rule_id": "E001",
                    "description": "RSI below 30",
                    "provenance": "explicit",
                    "evidence": "",
                }
            ],
        },
        "position_sizing": {"present": False, "rules": []},
        "risk_management": {"present": False, "rules": []},
        "exit": {"present": False, "rules": []},
        "portfolio": {"present": False, "rules": []},
        "backtesting": {"present": False, "rules": []},
    },
    "component_coverage": {
        "market_regime": "absent",
        "market_eligibility": "absent",
        "asset_selection": "absent",
        "entry_signal": "present",
        "position_sizing": "absent",
        "risk_management": "absent",
        "exit": "absent",
        "portfolio": "absent",
        "backtesting": "absent",
    },
    "missing_components": [],
    "methodology_assessment": {
        "implementability": "research_only",
        "suitable_for_backtest": False,
        "primary_strength": "Clear entry signal.",
        "primary_weakness": "Missing exit rules.",
        "key_missing_components": ["exit"],
    },
    "extraction_audit": {
        "extracted_at": "2026-01-01T00:00:00+00:00",
        "sections_used": ["sec1"],
        "sections_skipped": [],
        "sections_filtered_too_short": [],
        "total_input_tokens": 400,
        "total_output_tokens": 200,
    },
}


class TestStrategyReportCmd:
    def test_no_args_errors(self):
        runner = _make_runner()
        result = runner.invoke(cli, ["strategy", "report"])
        assert result.exit_code != 0

    def test_invalid_uuid(self):
        runner = _make_runner()
        result = runner.invoke(cli, ["strategy", "report", "not-a-uuid"])
        assert result.exit_code != 0
        assert "invalid UUID" in (result.stderr + result.output)

    def test_paper_no_spec(self):
        runner = _make_runner()
        with patch(
            "quantforge.cli.commands.strategy_report.get_session"
        ) as mock_ctx, patch(
            "quantforge.cli.commands.strategy_report.StrategyRepository"
        ) as mock_repo_cls:
            session = MagicMock()
            session.__enter__ = MagicMock(return_value=session)
            session.__exit__ = MagicMock(return_value=False)
            mock_ctx.return_value = session

            repo = MagicMock()
            repo.get_latest_base.return_value = None
            mock_repo_cls.return_value = repo

            result = runner.invoke(cli, ["strategy", "report", PAPER_ID])
            assert result.exit_code != 0
            assert "No base spec found" in (result.stderr + result.output)

    def test_terminal_output(self, tmp_path):
        runner = _make_runner()
        spec_file = tmp_path / "spec.json"
        spec_file.write_text(json.dumps(MINIMAL_SPEC), encoding="utf-8")
        record = _make_strategy_record(file_path=str(spec_file))

        with patch(
            "quantforge.cli.commands.strategy_report.get_session"
        ) as mock_ctx, patch(
            "quantforge.cli.commands.strategy_report.StrategyRepository"
        ) as mock_repo_cls:
            session = MagicMock()
            session.__enter__ = MagicMock(return_value=session)
            session.__exit__ = MagicMock(return_value=False)
            mock_ctx.return_value = session

            repo = MagicMock()
            repo.get_latest_base.return_value = record
            mock_repo_cls.return_value = repo

            result = runner.invoke(cli, ["strategy", "report", PAPER_ID])
            assert result.exit_code == 0
            assert "Strategy Specification Report" in result.output
            assert "RSI below 30" in result.output

    def test_json_output(self, tmp_path):
        runner = _make_runner()
        spec_file = tmp_path / "spec.json"
        spec_file.write_text(json.dumps(MINIMAL_SPEC), encoding="utf-8")
        record = _make_strategy_record(file_path=str(spec_file))

        with patch(
            "quantforge.cli.commands.strategy_report.get_session"
        ) as mock_ctx, patch(
            "quantforge.cli.commands.strategy_report.StrategyRepository"
        ) as mock_repo_cls:
            session = MagicMock()
            session.__enter__ = MagicMock(return_value=session)
            session.__exit__ = MagicMock(return_value=False)
            mock_ctx.return_value = session

            repo = MagicMock()
            repo.get_latest_base.return_value = record
            mock_repo_cls.return_value = repo

            result = runner.invoke(
                cli, ["strategy", "report", PAPER_ID, "--json"]
            )
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["strategy_id"] == STRATEGY_ID
            assert data["paper_id"] == PAPER_ID
            assert data["spec"] is not None

    def test_output_file_markdown(self, tmp_path):
        runner = _make_runner()
        spec_file = tmp_path / "spec.json"
        spec_file.write_text(json.dumps(MINIMAL_SPEC), encoding="utf-8")
        record = _make_strategy_record(file_path=str(spec_file))
        out_file = tmp_path / "report.md"

        with patch(
            "quantforge.cli.commands.strategy_report.get_session"
        ) as mock_ctx, patch(
            "quantforge.cli.commands.strategy_report.StrategyRepository"
        ) as mock_repo_cls:
            session = MagicMock()
            session.__enter__ = MagicMock(return_value=session)
            session.__exit__ = MagicMock(return_value=False)
            mock_ctx.return_value = session

            repo = MagicMock()
            repo.get_latest_base.return_value = record
            mock_repo_cls.return_value = repo

            result = runner.invoke(
                cli,
                ["strategy", "report", PAPER_ID, "--output-file", str(out_file)],
            )
            assert result.exit_code == 0
            assert out_file.exists()
            content = out_file.read_text(encoding="utf-8")
            assert "# Strategy Specification Report" in content

    def test_approved_flag_no_records(self):
        runner = _make_runner()
        with patch(
            "quantforge.cli.commands.strategy_report.get_session"
        ) as mock_ctx, patch(
            "quantforge.cli.commands.strategy_report.StrategyRepository"
        ) as mock_repo_cls:
            session = MagicMock()
            session.__enter__ = MagicMock(return_value=session)
            session.__exit__ = MagicMock(return_value=False)
            scalars_result = MagicMock()
            scalars_result.__iter__ = MagicMock(return_value=iter([]))
            session.scalars.return_value = scalars_result
            mock_ctx.return_value = session
            mock_repo_cls.return_value = MagicMock()

            result = runner.invoke(cli, ["strategy", "report", "--approved"])
            assert result.exit_code == 0
            assert "No approved base specs" in result.output

    def test_approved_flag_with_records(self, tmp_path):
        runner = _make_runner()
        spec_file = tmp_path / "spec.json"
        spec_file.write_text(json.dumps(MINIMAL_SPEC), encoding="utf-8")
        record = _make_strategy_record(file_path=str(spec_file))

        with patch(
            "quantforge.cli.commands.strategy_report.get_session"
        ) as mock_ctx, patch(
            "quantforge.cli.commands.strategy_report.StrategyRepository"
        ) as mock_repo_cls:
            session = MagicMock()
            session.__enter__ = MagicMock(return_value=session)
            session.__exit__ = MagicMock(return_value=False)
            scalars_result = MagicMock()
            scalars_result.__iter__ = MagicMock(return_value=iter([record]))
            session.scalars.return_value = scalars_result
            mock_ctx.return_value = session
            mock_repo_cls.return_value = MagicMock()

            result = runner.invoke(cli, ["strategy", "report", "--approved"])
            assert result.exit_code == 0
            assert "Strategy Specification Report" in result.output

    def test_missing_spec_file(self):
        runner = _make_runner()
        record = _make_strategy_record(file_path="/non/existent/file.json")

        with patch(
            "quantforge.cli.commands.strategy_report.get_session"
        ) as mock_ctx, patch(
            "quantforge.cli.commands.strategy_report.StrategyRepository"
        ) as mock_repo_cls:
            session = MagicMock()
            session.__enter__ = MagicMock(return_value=session)
            session.__exit__ = MagicMock(return_value=False)
            mock_ctx.return_value = session

            repo = MagicMock()
            repo.get_latest_base.return_value = record
            mock_repo_cls.return_value = repo

            result = runner.invoke(cli, ["strategy", "report", PAPER_ID])
            # Should succeed but note the missing file in the report
            assert result.exit_code == 0
            assert "missing or unreadable" in result.output or "Report for paper" in result.output
