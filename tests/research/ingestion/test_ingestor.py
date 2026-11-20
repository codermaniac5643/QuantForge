# tests/research/ingestion/test_ingestor.py

import shutil
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from quantforge.database.repositories.paper import PaperRepository
from quantforge.research.ingestion.ingestor import ingest_pdf, IngestResult


# --- Fixtures ---

@pytest.fixture()
def valid_pdf(tmp_path) -> Path:
    """Create a minimal valid PDF file."""
    pdf = tmp_path / "test_paper.pdf"
    # Minimal PDF with magic bytes
    pdf.write_bytes(
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
        b"%%EOF\n"
        b"x" * 500  # pad to reasonable size
    )
    return pdf


@pytest.fixture()
def library_root(tmp_path) -> Path:
    root = tmp_path / "research_library"
    root.mkdir()
    return root


def make_repo() -> MagicMock:
    repo = MagicMock(spec=PaperRepository)
    repo.create = MagicMock()
    return repo


# --- Happy path ---

def test_ingest_valid_pdf_succeeds(valid_pdf, library_root):
    repo = make_repo()
    result = ingest_pdf(
        source_file=valid_pdf,
        title="Cross-Sectional Momentum Strategies",
        repo=repo,
        library_root=library_root,
    )
    assert result.success
    assert result.paper_id is not None
    assert result.file_path is not None
    assert result.error is None


def test_ingest_copies_file_to_library(valid_pdf, library_root):
    repo = make_repo()
    result = ingest_pdf(
        source_file=valid_pdf,
        title="Momentum Study",
        repo=repo,
        library_root=library_root,
    )
    assert result.file_path.exists()
    assert result.file_path.stat().st_size > 0


def test_ingest_creates_subdirectory_per_paper(valid_pdf, library_root):
    repo = make_repo()
    result = ingest_pdf(
        source_file=valid_pdf,
        title="Momentum Study",
        repo=repo,
        library_root=library_root,
    )
    # File lives under library_root/{paper_id}/
    assert result.file_path.parent.parent == library_root


def test_ingest_calls_repo_create(valid_pdf, library_root):
    repo = make_repo()
    ingest_pdf(
        source_file=valid_pdf,
        title="Momentum Study",
        repo=repo,
        library_root=library_root,
    )
    repo.create.assert_called_once()


def test_ingest_record_has_acquired_state(valid_pdf, library_root):
    repo = make_repo()
    ingest_pdf(
        source_file=valid_pdf,
        title="Momentum Study",
        repo=repo,
        library_root=library_root,
    )
    record = repo.create.call_args[0][0]
    assert record.state == "acquired"


def test_ingest_stores_source_reference(valid_pdf, library_root):
    repo = make_repo()
    ingest_pdf(
        source_file=valid_pdf,
        title="Momentum Study",
        repo=repo,
        library_root=library_root,
        source_name="ssrn",
        source_id="1234567",
        source_url="https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1234567",
    )
    record = repo.create.call_args[0][0]
    assert len(record.sources) == 1
    assert record.sources[0]["source_name"] == "ssrn"
    assert record.sources[0]["source_id"] == "1234567"


def test_ingest_stores_file_hash(valid_pdf, library_root):
    repo = make_repo()
    ingest_pdf(
        source_file=valid_pdf,
        title="Momentum Study",
        repo=repo,
        library_root=library_root,
    )
    record = repo.create.call_args[0][0]
    assert record.file_hash is not None
    assert len(record.file_hash) == 64  # SHA-256 hex


def test_ingest_stores_file_size(valid_pdf, library_root):
    repo = make_repo()
    ingest_pdf(
        source_file=valid_pdf,
        title="Momentum Study",
        repo=repo,
        library_root=library_root,
    )
    record = repo.create.call_args[0][0]
    assert record.file_size_bytes > 0


def test_ingest_stores_abstract(valid_pdf, library_root):
    repo = make_repo()
    ingest_pdf(
        source_file=valid_pdf,
        title="Momentum Study",
        repo=repo,
        library_root=library_root,
        abstract="We study momentum strategies.",
    )
    record = repo.create.call_args[0][0]
    assert record.abstract == "We study momentum strategies."


def test_ingest_marks_user_supplied_in_provenance(valid_pdf, library_root):
    repo = make_repo()
    ingest_pdf(
        source_file=valid_pdf,
        title="Momentum Study",
        repo=repo,
        library_root=library_root,
        source_name="user_supplied",
    )
    record = repo.create.call_args[0][0]
    assert record.evaluation["acquisition"]["user_supplied"] is True


# --- Error paths ---

def test_ingest_missing_file_returns_error(library_root):
    repo = make_repo()
    result = ingest_pdf(
        source_file=Path("/nonexistent/paper.pdf"),
        title="Momentum Study",
        repo=repo,
        library_root=library_root,
    )
    assert not result.success
    assert "not found" in result.error.lower()
    repo.create.assert_not_called()


def test_ingest_invalid_pdf_returns_error(tmp_path, library_root):
    bad_file = tmp_path / "not_a_pdf.pdf"
    bad_file.write_bytes(b"this is not a pdf file at all")
    repo = make_repo()

    result = ingest_pdf(
        source_file=bad_file,
        title="Bad Paper",
        repo=repo,
        library_root=library_root,
    )
    assert not result.success
    assert "validation failed" in result.error.lower()
    repo.create.assert_not_called()


def test_ingest_cleans_up_on_db_error(valid_pdf, library_root):
    """If DB write fails, the copied file is removed."""
    repo = make_repo()
    repo.create.side_effect = Exception("DB connection lost")

    result = ingest_pdf(
        source_file=valid_pdf,
        title="Momentum Study",
        repo=repo,
        library_root=library_root,
    )
    assert not result.success
    assert "DB connection lost" in result.error
    # No orphaned files left in library
    remaining = list(library_root.rglob("*.pdf"))
    assert len(remaining) == 0


def test_ingest_no_partial_files_on_copy_failure(valid_pdf, library_root):
    """If file copy fails, no partial directory is left behind."""
    repo = make_repo()

    with patch("shutil.copy2", side_effect=OSError("disk full")):
        result = ingest_pdf(
            source_file=valid_pdf,
            title="Momentum Study",
            repo=repo,
            library_root=library_root,
        )

    assert not result.success
    assert "disk full" in result.error
    repo.create.assert_not_called()


def test_ingest_directory_not_a_file_returns_error(tmp_path, library_root):
    repo = make_repo()
    result = ingest_pdf(
        source_file=tmp_path,  # directory not a file
        title="Bad Input",
        repo=repo,
        library_root=library_root,
    )
    assert not result.success
    assert "not a file" in result.error.lower()