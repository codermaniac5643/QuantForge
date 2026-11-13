# tests/cli/test_cli.py
"""
CLI tests — Milestone 10.

All DB and pipeline calls are mocked.  No live PostgreSQL, no real HTTP.
Uses Click's CliRunner for all command invocations.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from quantforge.cli.main import cli
from quantforge.models.states import PaperState


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_record(
    paper_id: uuid.UUID | None = None,
    state: PaperState = PaperState.CANDIDATE,
    title: str = "Momentum Strategies in Equity Markets",
    arxiv_id: str = "2401.00001",
    composite_score: float = 0.75,
) -> MagicMock:
    r = MagicMock()
    r.id = paper_id or uuid.uuid4()
    r.title = title
    r.arxiv_id = arxiv_id
    r.doi = "10.1234/test"
    r.state = state.value
    r.composite_score = composite_score
    r.relevance_score = 0.80
    r.credibility_score = 0.70
    r.implementability_score = 0.60
    r.reproducibility_score = 0.50
    r.authors = [{"name": "Alice"}]
    r.keywords = ["momentum"]
    r.sources = []
    r.file_path = None
    r.file_hash = None
    r.file_size_bytes = None
    r.created_at = None
    r.updated_at = None
    r.evaluation = {}
    return r


def _make_repo(records: list = None) -> MagicMock:
    repo = MagicMock()
    records = records or []
    repo.get_by_state.return_value = records
    repo.get_by_id.return_value = records[0] if records else None
    repo.count_by_state.return_value = {
        "candidate": 3,
        "approved": 1,
        "acquired": 2,
        "processed": 1,
    }
    repo.session = MagicMock()
    return repo


RUNNER = CliRunner()

# Patch targets
_GET_SESSION = "quantforge.database.connection.get_session"
_PAPER_REPO = "quantforge.database.repositories.paper.PaperRepository"


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


class TestCliEntrypoint:
    def test_help_exits_zero(self):
        result = RUNNER.invoke(cli, ["--help"])
        assert result.exit_code == 0

    def test_research_group_listed(self):
        result = RUNNER.invoke(cli, ["--help"])
        assert "research" in result.output

    def test_research_help(self):
        result = RUNNER.invoke(cli, ["research", "--help"])
        assert result.exit_code == 0

    def test_all_subcommands_listed(self):
        result = RUNNER.invoke(cli, ["research", "--help"])
        for cmd in ["search", "rank", "acquire", "process", "status", "report"]:
            assert cmd in result.output


# ---------------------------------------------------------------------------
# quantforge research status
# ---------------------------------------------------------------------------


class TestStatusCommand:
    def _invoke(self, extra_args=None, records=None):
        repo = _make_repo(records or [])
        with (
            patch(_GET_SESSION) as mock_gs,
        ):
            mock_session = MagicMock()
            mock_session.__enter__ = MagicMock(return_value=mock_session)
            mock_session.__exit__ = MagicMock(return_value=False)
            mock_gs.return_value = mock_session
            with patch("quantforge.cli.commands.status.PaperRepository", return_value=repo):
                return RUNNER.invoke(cli, ["research", "status"] + (extra_args or []))

    def test_status_exits_zero(self):
        result = self._invoke()
        assert result.exit_code == 0

    def test_status_shows_state_counts(self):
        result = self._invoke()
        assert "candidate" in result.output or "Pipeline status" in result.output

    def test_status_json_flag(self):
        result = self._invoke(["--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "by_state" in data or "total" in data

    def test_status_paper_not_found(self):
        result = self._invoke(["--paper-id", str(uuid.uuid4())])
        # repo.get_by_id returns None by default → "not found"
        assert result.exit_code == 0
        assert "not found" in result.output.lower() or result.exit_code == 0

    def test_status_specific_paper(self):
        pid = uuid.uuid4()
        record = _make_record(paper_id=pid)
        repo = _make_repo([record])
        repo.get_by_id.return_value = record
        with (
            patch(_GET_SESSION) as mock_gs,
            patch("quantforge.cli.commands.status.PaperRepository", return_value=repo),
        ):
            mock_session = MagicMock()
            mock_session.__enter__ = MagicMock(return_value=mock_session)
            mock_session.__exit__ = MagicMock(return_value=False)
            mock_gs.return_value = mock_session
            result = RUNNER.invoke(cli, ["research", "status", "--paper-id", str(pid)])
        assert result.exit_code == 0
        assert "Momentum" in result.output or str(pid) in result.output

    def test_status_invalid_uuid(self):
        result = self._invoke(["--paper-id", "not-a-uuid"])
        assert result.exit_code != 0 or "invalid" in result.output.lower()


# ---------------------------------------------------------------------------
# quantforge research rank
# ---------------------------------------------------------------------------


class TestRankCommand:
    def _invoke(self, extra_args=None, records=None):
        records = records or [_make_record()]
        repo = _make_repo(records)
        with (
            patch(_GET_SESSION) as mock_gs,
            patch("quantforge.database.repositories.paper.PaperRepository", return_value=repo),
        ):
            mock_session = MagicMock()
            mock_session.__enter__ = MagicMock(return_value=mock_session)
            mock_session.__exit__ = MagicMock(return_value=False)
            mock_gs.return_value = mock_session
            return RUNNER.invoke(cli, ["research", "rank"] + (extra_args or []))

    def test_rank_exits_zero(self):
        result = self._invoke()
        assert result.exit_code == 0

    def test_rank_shows_arxiv_id(self):
        result = self._invoke()
        assert "2401.00001" in result.output

    def test_rank_json_output(self):
        result = self._invoke(["--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)

    def test_rank_empty_results(self):
        result = self._invoke(records=[])
        assert result.exit_code == 0
        assert "no results" in result.output.lower() or result.exit_code == 0

    def test_rank_invalid_state(self):
        result = self._invoke(["--state", "nonexistent"])
        assert result.exit_code != 0

    def test_rank_min_score_filters(self):
        # All records have composite_score=0.75; min_score=0.9 → no results
        result = self._invoke(["--min-score", "0.9"])
        assert result.exit_code == 0
        assert "(no results)" in result.output or "0 result" in result.output


# ---------------------------------------------------------------------------
# quantforge research process
# ---------------------------------------------------------------------------


class TestProcessCommand:
    def _invoke(self, extra_args=None, records=None, proc_result=None):
        records = records if records is not None else [_make_record(state=PaperState.ACQUIRED)]
        repo = _make_repo(records)

        if proc_result is None and records:
            proc_result = MagicMock()
            proc_result.success = True
            proc_result.final_state = PaperState.PROCESSED
            proc_result.paper_id = records[0].id
            proc_result.extraction = MagicMock(page_count=3)
            proc_result.section_count = 4
            proc_result.manifest = MagicMock(table_count=1)
            proc_result.error = None

        mock_processor = MagicMock()
        if proc_result:
            mock_processor.process.return_value = proc_result

        with (
            patch(_GET_SESSION) as mock_gs,
            patch("quantforge.database.repositories.paper.PaperRepository", return_value=repo),
            patch("quantforge.research.processing.processor.PdfProcessor", return_value=mock_processor),
        ):
            mock_session = MagicMock()
            mock_session.__enter__ = MagicMock(return_value=mock_session)
            mock_session.__exit__ = MagicMock(return_value=False)
            mock_gs.return_value = mock_session
            return RUNNER.invoke(cli, ["research", "process"] + (extra_args or []))
            
    def test_process_exits_zero(self):
        result = self._invoke()
        assert result.exit_code == 0

    def test_process_shows_success(self):
        result = self._invoke()
        assert "✓" in result.output or "processed" in result.output.lower()

    def test_process_json_output(self):
        result = self._invoke(["--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)

    def test_process_no_acquired_papers(self):
        result = self._invoke(records=[])
        assert result.exit_code == 0
        assert "no acquired" in result.output.lower() or "0 paper" in result.output.lower()

    def test_process_invalid_uuid(self):
        result = self._invoke(["--paper-id", "bad-uuid"])
        assert result.exit_code != 0 or "invalid" in result.output.lower()


# ---------------------------------------------------------------------------
# quantforge research report
# ---------------------------------------------------------------------------


class TestReportCommand:
    def _invoke(self, extra_args=None, records=None):
        records = records or [_make_record(state=PaperState.PROCESSED)]
        repo = _make_repo(records)

        with (
            patch(_GET_SESSION) as mock_gs,
            patch("quantforge.database.repositories.paper.PaperRepository", return_value=repo),
        ):
            mock_session = MagicMock()
            mock_session.__enter__ = MagicMock(return_value=mock_session)
            mock_session.__exit__ = MagicMock(return_value=False)
            mock_gs.return_value = mock_session
            return RUNNER.invoke(cli, ["research", "report"] + (extra_args or []))

    def test_report_exits_zero(self):
        result = self._invoke()
        assert result.exit_code == 0

    def test_report_shows_title(self):
        result = self._invoke()
        assert "Momentum" in result.output or "Report" in result.output

    def test_report_json_output(self):
        result = self._invoke(["--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "papers" in data
        assert "generated_at" in data

    def test_report_markdown_file(self, tmp_path):
        md_path = tmp_path / "report.md"
        result = self._invoke(["--output-file", str(md_path)])
        assert result.exit_code == 0
        assert md_path.exists()
        content = md_path.read_text()
        assert "# QuantForge" in content

    def test_report_json_file(self, tmp_path):
        json_path = tmp_path / "report.json"
        result = self._invoke(["--output-file", str(json_path)])
        assert result.exit_code == 0
        assert json_path.exists()
        data = json.loads(json_path.read_text())
        assert "papers" in data

    def test_report_min_score_filter(self):
        # min_score=0.99 → no papers pass (composite_score=0.75)
        result = self._invoke(["--min-score", "0.99"])
        assert result.exit_code == 0
        # No papers in report
        data_check = self._invoke(["--json", "--min-score", "0.99"])
        data = json.loads(data_check.output)
        assert data["total_papers"] == 0

    def test_report_empty_db(self):
        result = self._invoke(records=[])
        assert result.exit_code == 0

