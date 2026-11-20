# tests/strategy/test_strategy_extract_cli.py
"""
Tests for `quantforge strategy extract single` and `extract batch` commands.

All LLM calls and DB writes are mocked — no real extraction runs.
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_runner() -> CliRunner:
    return CliRunner()


def _mock_paper(paper_id_str: str):
    record = MagicMock()
    record.id = uuid.UUID(paper_id_str)
    record.state = "processed"
    return record


def _make_success_summary(paper_id_str: str) -> dict:
    return {
        "paper_id": paper_id_str,
        "strategy_id": str(uuid.uuid4()),
        "version": 1,
        "file_path": f"/tmp/{paper_id_str}_v1.json",
        "outcome": "success",
        "completeness_class": "full",
        "tokens_in": 500,
        "tokens_out": 300,
    }


# ---------------------------------------------------------------------------
# extract single
# ---------------------------------------------------------------------------


class TestExtractSingle:
    def test_invalid_uuid(self):
        runner = _make_runner()
        result = runner.invoke(cli, ["strategy", "extract", "single", "not-a-uuid"])
        assert result.exit_code != 0
        assert "invalid UUID" in result.stderr or "invalid UUID" in result.output

    def test_paper_not_found(self):
        runner = _make_runner()
        with patch(
            "quantforge.cli.commands.strategy_extract.get_session"
        ) as mock_sess_ctx:
            session = MagicMock()
            session.__enter__ = MagicMock(return_value=session)
            session.__exit__ = MagicMock(return_value=False)
            mock_sess_ctx.return_value = session
            with patch(
                "quantforge.cli.commands.strategy_extract.PaperRepository"
            ) as mock_repo_cls:
                repo = MagicMock()
                repo.get_by_id.return_value = None
                mock_repo_cls.return_value = repo

                result = runner.invoke(
                    cli, ["strategy", "extract", "single", PAPER_ID]
                )
                assert result.exit_code != 0
                assert "not found" in result.stderr or "not found" in result.output

    def test_package_dir_missing(self, tmp_path):
        runner = _make_runner()
        paper = _mock_paper(PAPER_ID)

        with patch(
            "quantforge.cli.commands.strategy_extract.get_session"
        ) as mock_sess_ctx, patch(
            "quantforge.cli.commands.strategy_extract.PaperRepository"
        ) as mock_repo_cls:
            session = MagicMock()
            session.__enter__ = MagicMock(return_value=session)
            session.__exit__ = MagicMock(return_value=False)
            mock_sess_ctx.return_value = session
            repo = MagicMock()
            repo.get_by_id.return_value = paper
            mock_repo_cls.return_value = repo

            # library root has no package subdir
            result = runner.invoke(
                cli,
                [
                    "strategy", "extract", "single", PAPER_ID,
                    "--library-root", str(tmp_path),
                ],
            )
            assert result.exit_code != 0
            assert "no Research Document Package" in (result.stderr + result.output)

    def test_success(self, tmp_path):
        runner = _make_runner()
        paper = _mock_paper(PAPER_ID)
        # Create a fake package dir
        pkg_dir = tmp_path / PAPER_ID
        pkg_dir.mkdir()

        summary = _make_success_summary(PAPER_ID)

        with patch(
            "quantforge.cli.commands.strategy_extract.get_session"
        ) as mock_sess_ctx, patch(
            "quantforge.cli.commands.strategy_extract.PaperRepository"
        ) as mock_repo_cls, patch(
            "quantforge.cli.commands.strategy_extract._run_one_paper",
            return_value=summary,
        ):
            session = MagicMock()
            session.__enter__ = MagicMock(return_value=session)
            session.__exit__ = MagicMock(return_value=False)
            mock_sess_ctx.return_value = session
            repo = MagicMock()
            repo.get_by_id.return_value = paper
            mock_repo_cls.return_value = repo

            result = runner.invoke(
                cli,
                [
                    "strategy", "extract", "single", PAPER_ID,
                    "--library-root", str(tmp_path),
                ],
            )
            assert result.exit_code == 0
            assert "success" in result.output

    def test_json_output(self, tmp_path):
        runner = _make_runner()
        paper = _mock_paper(PAPER_ID)
        pkg_dir = tmp_path / PAPER_ID
        pkg_dir.mkdir()

        summary = _make_success_summary(PAPER_ID)

        with patch(
            "quantforge.cli.commands.strategy_extract.get_session"
        ) as mock_sess_ctx, patch(
            "quantforge.cli.commands.strategy_extract.PaperRepository"
        ) as mock_repo_cls, patch(
            "quantforge.cli.commands.strategy_extract._run_one_paper",
            return_value=summary,
        ):
            session = MagicMock()
            session.__enter__ = MagicMock(return_value=session)
            session.__exit__ = MagicMock(return_value=False)
            mock_sess_ctx.return_value = session
            repo = MagicMock()
            repo.get_by_id.return_value = paper
            mock_repo_cls.return_value = repo

            result = runner.invoke(
                cli,
                [
                    "strategy", "extract", "single", PAPER_ID,
                    "--library-root", str(tmp_path),
                    "--json",
                ],
            )
            assert result.exit_code == 0
            # CliRunner merges stderr; find the JSON object line
            json_line = next(
                (l for l in result.output.splitlines() if l.startswith("{") or l.startswith("[")),
                None,
            )
            # print_json uses json.dumps over multiple lines; parse whole output after the banner
            import re
            match = re.search(r"\{.*\}", result.output, re.DOTALL)
            assert match is not None, f"No JSON found in output: {result.output!r}"
            data = json.loads(match.group())
            assert data["outcome"] == "success"
            assert data["paper_id"] == PAPER_ID

    def test_extraction_failure_exits_nonzero(self, tmp_path):
        runner = _make_runner()
        paper = _mock_paper(PAPER_ID)
        pkg_dir = tmp_path / PAPER_ID
        pkg_dir.mkdir()

        summary = {
            "paper_id": PAPER_ID,
            "outcome": "pipeline_error",
            "error": "LLM unreachable",
        }

        with patch(
            "quantforge.cli.commands.strategy_extract.get_session"
        ) as mock_sess_ctx, patch(
            "quantforge.cli.commands.strategy_extract.PaperRepository"
        ) as mock_repo_cls, patch(
            "quantforge.cli.commands.strategy_extract._run_one_paper",
            return_value=summary,
        ):
            session = MagicMock()
            session.__enter__ = MagicMock(return_value=session)
            session.__exit__ = MagicMock(return_value=False)
            mock_sess_ctx.return_value = session
            repo = MagicMock()
            repo.get_by_id.return_value = paper
            mock_repo_cls.return_value = repo

            result = runner.invoke(
                cli,
                [
                    "strategy", "extract", "single", PAPER_ID,
                    "--library-root", str(tmp_path),
                ],
            )
            assert result.exit_code != 0


# ---------------------------------------------------------------------------
# extract batch
# ---------------------------------------------------------------------------


class TestExtractBatch:
    def test_invalid_state(self):
        runner = _make_runner()
        result = runner.invoke(
            cli, ["strategy", "extract", "batch", "--state", "bogus"]
        )
        assert result.exit_code != 0
        assert "unknown state" in (result.stderr + result.output)

    def test_no_papers(self, tmp_path):
        runner = _make_runner()

        with patch(
            "quantforge.cli.commands.strategy_extract.get_session"
        ) as mock_sess_ctx, patch(
            "quantforge.cli.commands.strategy_extract.StrategyRepository"
        ):
            session = MagicMock()
            session.__enter__ = MagicMock(return_value=session)
            session.__exit__ = MagicMock(return_value=False)
            session.scalars.return_value = iter([])
            mock_sess_ctx.return_value = session

            result = runner.invoke(
                cli,
                [
                    "strategy", "extract", "batch",
                    "--library-root", str(tmp_path),
                ],
            )
            assert result.exit_code == 0
            assert "No papers" in result.output

    def test_batch_success(self, tmp_path):
        runner = _make_runner()

        paper = _mock_paper(PAPER_ID)
        pkg_dir = tmp_path / PAPER_ID
        pkg_dir.mkdir()

        summary = _make_success_summary(PAPER_ID)

        with patch(
            "quantforge.cli.commands.strategy_extract.get_session"
        ) as mock_sess_ctx, patch(
            "quantforge.cli.commands.strategy_extract.StrategyRepository"
        ) as mock_strategy_repo_cls, patch(
            "quantforge.cli.commands.strategy_extract._run_one_paper",
            return_value=summary,
        ):
            session = MagicMock()
            session.__enter__ = MagicMock(return_value=session)
            session.__exit__ = MagicMock(return_value=False)
            session.scalars.return_value = iter([paper])
            mock_sess_ctx.return_value = session

            strategy_repo = MagicMock()
            strategy_repo.get_by_paper_id.return_value = []
            mock_strategy_repo_cls.return_value = strategy_repo

            result = runner.invoke(
                cli,
                [
                    "strategy", "extract", "batch",
                    "--library-root", str(tmp_path),
                    "--no-skip-existing",
                ],
            )
            assert result.exit_code == 0
            assert "success" in result.output

    def test_batch_json_output(self, tmp_path):
        runner = _make_runner()
        paper = _mock_paper(PAPER_ID)
        pkg_dir = tmp_path / PAPER_ID
        pkg_dir.mkdir()

        summary = _make_success_summary(PAPER_ID)

        with patch(
            "quantforge.cli.commands.strategy_extract.get_session"
        ) as mock_sess_ctx, patch(
            "quantforge.cli.commands.strategy_extract.StrategyRepository"
        ) as mock_strategy_repo_cls, patch(
            "quantforge.cli.commands.strategy_extract._run_one_paper",
            return_value=summary,
        ):
            session = MagicMock()
            session.__enter__ = MagicMock(return_value=session)
            session.__exit__ = MagicMock(return_value=False)
            # Use a list so the mock isn't exhausted on first access
            session.scalars.return_value = [paper]
            mock_sess_ctx.return_value = session

            strategy_repo = MagicMock()
            strategy_repo.get_by_paper_id.return_value = []
            mock_strategy_repo_cls.return_value = strategy_repo

            result = runner.invoke(
                cli,
                [
                    "strategy", "extract", "batch",
                    "--library-root", str(tmp_path),
                    "--json",
                    "--no-skip-existing",
                ],
            )
            assert result.exit_code == 0
            # CliRunner merges stderr; extract JSON portion from output
            import re
            match = re.search(r"\{.*\}", result.output, re.DOTALL)
            assert match is not None, f"No JSON in output: {result.output!r}"
            data = json.loads(match.group())
            assert "summaries" in data
            assert "totals" in data
