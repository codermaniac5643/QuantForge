# tests/strategy/test_review_cli.py
"""
CLI tests for `quantforge strategy review` subcommands — Milestone 15.

Uses Click's test runner (CliRunner) with mocked ReviewService
so no real DB or filesystem is required.
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
    StrategyStatus,
)
from quantforge.strategy.review import ChecklistItem, ReviewChecklist, ReviewSummary


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def runner():
    return CliRunner()


def _make_record(
    strategy_id: uuid.UUID | None = None,
    paper_id: uuid.UUID | None = None,
    status: StrategyStatus = StrategyStatus.UNDER_REVIEW,
    file_path: str | None = "/tmp/spec_v1.json",
) -> StrategyRecord:
    r = MagicMock(spec=StrategyRecord)
    r.id = strategy_id or uuid.uuid4()
    r.paper_id = paper_id or uuid.uuid4()
    r.version = 1
    r.variant = "base"
    r.status = status.value
    r.completeness_class = CompletenessClass.ENTRY_FOCUSED.value
    r.extracted_by = ExtractionMethod.LLM_ASSISTED.value
    r.reviewed_by = None
    r.file_path = file_path
    r.created_at = "2024-01-01T00:00:00+00:00"
    r.updated_at = "2024-01-01T00:00:00+00:00"
    return r


def _passing_checklist() -> ReviewChecklist:
    items = [
        ChecklistItem(number=i, description=f"Check {i}", passed=True)
        for i in range(1, 7)
    ]
    return ReviewChecklist(items=items)


def _failing_checklist() -> ReviewChecklist:
    items = [
        ChecklistItem(number=1, description="Check 1", passed=False, detail="Bad provenance"),
        *[ChecklistItem(number=i, description=f"Check {i}", passed=True) for i in range(2, 7)],
    ]
    return ReviewChecklist(items=items)


def _make_summary(
    record=None,
    checklist=None,
    spec=None,
    load_error=None,
) -> ReviewSummary:
    return ReviewSummary(
        record=record or _make_record(),
        spec=spec,
        checklist=checklist or _passing_checklist(),
        load_error=load_error,
    )


# ---------------------------------------------------------------------------
# `quantforge strategy review list`
# ---------------------------------------------------------------------------


class TestReviewListCmd:
    def test_list_empty(self, runner):
        with patch("quantforge.cli.commands.strategy_review.get_session") as mock_gs:
            mock_gs.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_gs.return_value.__exit__ = MagicMock(return_value=False)
            with patch("quantforge.cli.commands.strategy_review.ReviewService") as MockSvc:
                MockSvc.return_value.list_pending_review.return_value = []
                result = runner.invoke(cli, ["strategy", "review", "list"])
        assert result.exit_code == 0
        assert "No specs" in result.output

    def test_list_shows_records(self, runner):
        record = _make_record()
        with patch("quantforge.cli.commands.strategy_review.get_session") as mock_gs:
            mock_gs.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_gs.return_value.__exit__ = MagicMock(return_value=False)
            with patch("quantforge.cli.commands.strategy_review.ReviewService") as MockSvc:
                MockSvc.return_value.list_pending_review.return_value = [record]
                result = runner.invoke(cli, ["strategy", "review", "list"])
        assert result.exit_code == 0
        assert "1 spec" in result.output

    def test_list_json_output(self, runner):
        record = _make_record()
        with patch("quantforge.cli.commands.strategy_review.get_session") as mock_gs:
            mock_gs.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_gs.return_value.__exit__ = MagicMock(return_value=False)
            with patch("quantforge.cli.commands.strategy_review.ReviewService") as MockSvc:
                MockSvc.return_value.list_pending_review.return_value = [record]
                result = runner.invoke(cli, ["strategy", "review", "list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 1


# ---------------------------------------------------------------------------
# `quantforge strategy review show`
# ---------------------------------------------------------------------------


class TestReviewShowCmd:
    def test_show_invalid_uuid(self, runner):
        result = runner.invoke(cli, ["strategy", "review", "show", "not-a-uuid"])
        assert result.exit_code != 0

    def test_show_terminal_output(self, runner):
        sid = uuid.uuid4()
        summary = _make_summary(record=_make_record(strategy_id=sid))
        with patch("quantforge.cli.commands.strategy_review.get_session") as mock_gs:
            mock_gs.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_gs.return_value.__exit__ = MagicMock(return_value=False)
            with patch("quantforge.cli.commands.strategy_review.ReviewService") as MockSvc:
                MockSvc.return_value.get_review_summary.return_value = summary
                result = runner.invoke(cli, ["strategy", "review", "show", str(sid)])
        assert result.exit_code == 0
        assert "Checklist" in result.output

    def test_show_json_output(self, runner):
        sid = uuid.uuid4()
        summary = _make_summary(record=_make_record(strategy_id=sid))
        with patch("quantforge.cli.commands.strategy_review.get_session") as mock_gs:
            mock_gs.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_gs.return_value.__exit__ = MagicMock(return_value=False)
            with patch("quantforge.cli.commands.strategy_review.ReviewService") as MockSvc:
                MockSvc.return_value.get_review_summary.return_value = summary
                result = runner.invoke(
                    cli, ["strategy", "review", "show", str(sid), "--json"]
                )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["checklist_passed"] is True

    def test_show_output_file(self, runner, tmp_path):
        sid = uuid.uuid4()
        out = tmp_path / "review.md"
        summary = _make_summary(record=_make_record(strategy_id=sid))
        with patch("quantforge.cli.commands.strategy_review.get_session") as mock_gs:
            mock_gs.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_gs.return_value.__exit__ = MagicMock(return_value=False)
            with patch("quantforge.cli.commands.strategy_review.ReviewService") as MockSvc:
                MockSvc.return_value.get_review_summary.return_value = summary
                result = runner.invoke(
                    cli,
                    ["strategy", "review", "show", str(sid), "--output-file", str(out)],
                )
        assert result.exit_code == 0
        assert out.exists()
        assert "Review Sheet" in out.read_text()


# ---------------------------------------------------------------------------
# `quantforge strategy review approve`
# ---------------------------------------------------------------------------


class TestReviewApproveCmd:
    def test_approve_invalid_uuid(self, runner):
        result = runner.invoke(
            cli,
            ["strategy", "review", "approve", "bad-uuid", "--reviewer", "me"],
        )
        assert result.exit_code != 0

    def test_approve_success_with_yes_flag(self, runner):
        sid = uuid.uuid4()
        record = _make_record(strategy_id=sid)
        summary = _make_summary(record=record)
        with patch("quantforge.cli.commands.strategy_review.get_session") as mock_gs:
            mock_session = MagicMock()
            mock_gs.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_gs.return_value.__exit__ = MagicMock(return_value=False)
            with patch("quantforge.cli.commands.strategy_review.ReviewService") as MockSvc:
                svc = MockSvc.return_value
                svc.get_review_summary.return_value = summary
                svc.approve_spec.return_value = record
                result = runner.invoke(
                    cli,
                    [
                        "strategy", "review", "approve", str(sid),
                        "--reviewer", "analyst_01", "--yes",
                    ],
                )
        assert result.exit_code == 0
        assert "approved" in result.output

    def test_approve_fails_when_checklist_fails(self, runner):
        sid = uuid.uuid4()
        record = _make_record(strategy_id=sid)
        summary = _make_summary(record=record, checklist=_failing_checklist())
        with patch("quantforge.cli.commands.strategy_review.get_session") as mock_gs:
            mock_gs.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_gs.return_value.__exit__ = MagicMock(return_value=False)
            with patch("quantforge.cli.commands.strategy_review.ReviewService") as MockSvc:
                MockSvc.return_value.get_review_summary.return_value = summary
                result = runner.invoke(
                    cli,
                    [
                        "strategy", "review", "approve", str(sid),
                        "--reviewer", "analyst_01", "--yes",
                    ],
                )
        assert result.exit_code != 0
        assert "Cannot approve" in result.output or "checklist" in result.output.lower()


# ---------------------------------------------------------------------------
# `quantforge strategy review reject`
# ---------------------------------------------------------------------------


class TestReviewRejectCmd:
    def test_reject_invalid_uuid(self, runner):
        result = runner.invoke(
            cli,
            ["strategy", "review", "reject", "bad-uuid",
             "--reviewer", "me", "--notes", "bad"],
        )
        assert result.exit_code != 0

    def test_reject_success(self, runner):
        sid = uuid.uuid4()
        record = _make_record(strategy_id=sid, status=StrategyStatus.REJECTED)
        with patch("quantforge.cli.commands.strategy_review.get_session") as mock_gs:
            mock_session = MagicMock()
            mock_gs.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_gs.return_value.__exit__ = MagicMock(return_value=False)
            with patch("quantforge.cli.commands.strategy_review.ReviewService") as MockSvc:
                MockSvc.return_value.reject_spec.return_value = record
                result = runner.invoke(
                    cli,
                    [
                        "strategy", "review", "reject", str(sid),
                        "--reviewer", "analyst_02",
                        "--notes", "Exit rules missing.",
                    ],
                )
        assert result.exit_code == 0
        assert "rejected" in result.output

    def test_reject_propagates_service_error(self, runner):
        sid = uuid.uuid4()
        with patch("quantforge.cli.commands.strategy_review.get_session") as mock_gs:
            mock_session = MagicMock()
            mock_gs.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_gs.return_value.__exit__ = MagicMock(return_value=False)
            with patch("quantforge.cli.commands.strategy_review.ReviewService") as MockSvc:
                MockSvc.return_value.reject_spec.side_effect = ValueError("non-empty notes")
                result = runner.invoke(
                    cli,
                    [
                        "strategy", "review", "reject", str(sid),
                        "--reviewer", "analyst_02",
                        "--notes", "",
                    ],
                )
        assert result.exit_code != 0
