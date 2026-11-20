# tests/cli/test_ingest.py

import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from quantforge.cli.main import cli
from quantforge.research.ingestion.ingestor import IngestResult

RUNNER = CliRunner()
_GET_SESSION = "quantforge.database.connection.get_session"
_INGEST_FN = "quantforge.research.ingestion.ingestor.ingest_pdf"
_PROCESSOR = "quantforge.research.processing.processor.PdfProcessor"


def make_mock_session():
    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    return mock_session


def make_success_result(tmp_path: Path) -> IngestResult:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n" + b"x" * 100)
    return IngestResult(
        success=True,
        paper_id=uuid.uuid4(),
        file_path=pdf,
    )


# --- Help ---

def test_ingest_help():
    result = RUNNER.invoke(cli, ["research", "ingest", "--help"])
    assert result.exit_code == 0
    assert "--file" in result.output
    assert "--title" in result.output


# --- Happy path ---

def test_ingest_succeeds(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n" + b"x" * 100)
    ingest_result = make_success_result(tmp_path)

    with (
        patch(_GET_SESSION) as mock_gs,
        patch(_INGEST_FN, return_value=ingest_result),
        patch("quantforge.database.repositories.paper.PaperRepository"),
    ):
        mock_gs.return_value = make_mock_session()
        result = RUNNER.invoke(cli, [
            "research", "ingest",
            "--file", str(pdf),
            "--title", "Momentum Strategies",
        ])

    assert result.exit_code == 0
    assert "Ingested" in result.output


def test_ingest_shows_paper_id(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n" + b"x" * 100)
    ingest_result = make_success_result(tmp_path)
    paper_id_str = str(ingest_result.paper_id)

    with (
        patch(_GET_SESSION) as mock_gs,
        patch(_INGEST_FN, return_value=ingest_result),
        patch("quantforge.database.repositories.paper.PaperRepository"),
    ):
        mock_gs.return_value = make_mock_session()
        result = RUNNER.invoke(cli, [
            "research", "ingest",
            "--file", str(pdf),
            "--title", "Momentum Strategies",
        ])

    assert paper_id_str in result.output


def test_ingest_json_output(tmp_path):
    import json
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n" + b"x" * 100)
    ingest_result = make_success_result(tmp_path)

    with (
        patch(_GET_SESSION) as mock_gs,
        patch(_INGEST_FN, return_value=ingest_result),
        patch("quantforge.database.repositories.paper.PaperRepository"),
    ):
        mock_gs.return_value = make_mock_session()
        result = RUNNER.invoke(cli, [
            "research", "ingest",
            "--file", str(pdf),
            "--title", "Momentum Strategies",
            "--json",
        ])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["success"] is True
    assert "paper_id" in data
    assert data["state"] == "acquired"


def test_ingest_shows_next_step(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n" + b"x" * 100)
    ingest_result = make_success_result(tmp_path)

    with (
        patch(_GET_SESSION) as mock_gs,
        patch(_INGEST_FN, return_value=ingest_result),
        patch("quantforge.database.repositories.paper.PaperRepository"),
    ):
        mock_gs.return_value = make_mock_session()
        result = RUNNER.invoke(cli, [
            "research", "ingest",
            "--file", str(pdf),
            "--title", "Momentum Strategies",
        ])

    assert "quantforge research process" in result.output


# --- With --process flag ---

def test_ingest_with_process_flag(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n" + b"x" * 100)
    ingest_result = make_success_result(tmp_path)

    mock_proc_result = MagicMock()
    mock_proc_result.success = True
    mock_proc_result.section_count = 8
    mock_proc_result.page_count = 24
    mock_proc_result.char_count = 45000

    mock_processor = MagicMock()
    mock_processor.process.return_value = mock_proc_result

    with (
        patch(_GET_SESSION) as mock_gs,
        patch(_INGEST_FN, return_value=ingest_result),
        patch("quantforge.database.repositories.paper.PaperRepository"),
        patch(_PROCESSOR, return_value=mock_processor),
    ):
        mock_gs.return_value = make_mock_session()
        result = RUNNER.invoke(cli, [
            "research", "ingest",
            "--file", str(pdf),
            "--title", "Momentum Strategies",
            "--process",
        ])

    assert result.exit_code == 0
    assert "PROCESSED" in result.output


def test_ingest_with_process_flag_json(tmp_path):
    import json
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n" + b"x" * 100)
    ingest_result = make_success_result(tmp_path)

    mock_proc_result = MagicMock()
    mock_proc_result.success = True
    mock_proc_result.section_count = 8
    mock_proc_result.page_count = 24
    mock_proc_result.char_count = 45000

    mock_processor = MagicMock()
    mock_processor.process.return_value = mock_proc_result

    with (
        patch(_GET_SESSION) as mock_gs,
        patch(_INGEST_FN, return_value=ingest_result),
        patch("quantforge.database.repositories.paper.PaperRepository"),
        patch(_PROCESSOR, return_value=mock_processor),
    ):
        mock_gs.return_value = make_mock_session()
        result = RUNNER.invoke(cli, [
            "research", "ingest",
            "--file", str(pdf),
            "--title", "Momentum Strategies",
            "--process",
            "--json",
        ])

    data = json.loads(result.output)
    assert data["processing_success"] is True
    assert data["sections"] == 8


# --- Error paths ---

def test_ingest_failure_exits_nonzero(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n" + b"x" * 100)
    fail_result = IngestResult(success=False, error="PDF validation failed: not a valid PDF")

    with (
        patch(_GET_SESSION) as mock_gs,
        patch(_INGEST_FN, return_value=fail_result),
        patch("quantforge.database.repositories.paper.PaperRepository"),
    ):
        mock_gs.return_value = make_mock_session()
        result = RUNNER.invoke(cli, [
            "research", "ingest",
            "--file", str(pdf),
            "--title", "Bad Paper",
        ])

    assert result.exit_code != 0


def test_ingest_failure_json_output(tmp_path):
    import json
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n" + b"x" * 100)
    fail_result = IngestResult(success=False, error="DB connection lost")

    with (
        patch(_GET_SESSION) as mock_gs,
        patch(_INGEST_FN, return_value=fail_result),
        patch("quantforge.database.repositories.paper.PaperRepository"),
    ):
        mock_gs.return_value = make_mock_session()
        result = RUNNER.invoke(cli, [
            "research", "ingest",
            "--file", str(pdf),
            "--title", "Bad Paper",
            "--json",
        ])

    data = json.loads(result.output)
    assert data["success"] is False
    assert "DB connection lost" in data["error"]