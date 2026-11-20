# tests/strategy/test_strategy_status_cli.py
"""
Tests for `quantforge strategy status`.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from quantforge.cli.main import cli


def _make_runner() -> CliRunner:
    return CliRunner()


class TestStrategyStatusCmd:
    def _patch_session(self, counts_by_status: dict[str, int], pending: int = 0):
        """
        Returns a context-manager mock whose session.execute() returns
        the given status counts, and session.scalar() returns pending count.
        """
        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)

        # session.execute() for count-by-status query
        rows = [MagicMock(status=k, count=v) for k, v in counts_by_status.items()]
        execute_result = MagicMock()
        execute_result.all.return_value = rows
        session.execute.return_value = execute_result

        # session.scalar() — first call is pending count, next calls for flagged
        session.scalar.side_effect = [pending]

        # session.scalars() — active records for flagged rule scan (empty list)
        scalars_result = MagicMock()
        scalars_result.__iter__ = MagicMock(return_value=iter([]))
        session.scalars.return_value = scalars_result

        return session

    def test_empty_database(self):
        runner = _make_runner()
        with patch(
            "quantforge.cli.commands.strategy_status.get_session"
        ) as mock_ctx:
            session = self._patch_session({})
            mock_ctx.return_value = session

            result = runner.invoke(cli, ["strategy", "status"])
            assert result.exit_code == 0
            assert "0 spec(s)" in result.output

    def test_shows_counts(self):
        runner = _make_runner()
        with patch(
            "quantforge.cli.commands.strategy_status.get_session"
        ) as mock_ctx:
            session = self._patch_session(
                {"draft": 3, "under_review": 2, "approved": 1}, pending=2
            )
            mock_ctx.return_value = session

            result = runner.invoke(cli, ["strategy", "status"])
            assert result.exit_code == 0
            assert "draft" in result.output
            assert "under_review" in result.output
            assert "approved" in result.output

    def test_json_output(self):
        runner = _make_runner()
        with patch(
            "quantforge.cli.commands.strategy_status.get_session"
        ) as mock_ctx:
            session = self._patch_session(
                {"draft": 2, "approved": 1}, pending=0
            )
            mock_ctx.return_value = session

            result = runner.invoke(cli, ["strategy", "status", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert "total_specs" in data
            assert "by_status" in data
            assert "pending_review" in data
            assert "flagged_rules" in data
            assert data["total_specs"] == 3

    def test_pending_review_shown(self):
        runner = _make_runner()
        with patch(
            "quantforge.cli.commands.strategy_status.get_session"
        ) as mock_ctx:
            session = self._patch_session(
                {"under_review": 5}, pending=5
            )
            mock_ctx.return_value = session

            result = runner.invoke(cli, ["strategy", "status"])
            assert result.exit_code == 0
            assert "5" in result.output

    def test_count_flagged_rules_missing_file(self):
        """_count_flagged_rules returns 0 for a missing file."""
        from quantforge.cli.commands.strategy_status import _count_flagged_rules

        assert _count_flagged_rules(None) == 0
        assert _count_flagged_rules("/non/existent/path.json") == 0

    def test_count_flagged_rules_inferred_no_evidence(self, tmp_path):
        from quantforge.cli.commands.strategy_status import _count_flagged_rules

        spec = {
            "pillars": {
                "entry_signal": {
                    "rules": [
                        {
                            "rule_id": "E001",
                            "provenance": "inferred",
                            "evidence": "",  # missing evidence
                        },
                        {
                            "rule_id": "E002",
                            "provenance": "explicit",
                            "evidence": "",
                        },
                    ]
                }
            }
        }
        f = tmp_path / "spec.json"
        f.write_text(json.dumps(spec), encoding="utf-8")
        assert _count_flagged_rules(str(f)) == 1

    def test_count_flagged_rules_quantforge_added(self, tmp_path):
        from quantforge.cli.commands.strategy_status import _count_flagged_rules

        spec = {
            "pillars": {
                "exit": {
                    "rules": [
                        {
                            "rule_id": "X001",
                            "provenance": "quantforge_added",
                        }
                    ]
                }
            }
        }
        f = tmp_path / "spec.json"
        f.write_text(json.dumps(spec), encoding="utf-8")
        assert _count_flagged_rules(str(f)) == 1
