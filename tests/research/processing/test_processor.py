# tests/research/processing/test_processor.py
"""
Tests for src/quantforge/research/processing/processor.py

The DB and PDF are mocked — no live PostgreSQL or real PDF needed.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from quantforge.models.states import PaperState
from quantforge.research.processing.extractor import ExtractionResult, PROCESSING_VERSION
from quantforge.research.processing.packager import PackageManifest
from quantforge.research.processing.processor import PdfProcessor, ProcessingResult

FIXTURE_PDF = Path(__file__).parent / "sample.pdf"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(
    paper_id: uuid.UUID,
    state: PaperState = PaperState.ACQUIRED,
    file_path: str | None = None,
) -> MagicMock:
    record = MagicMock()
    record.id = paper_id
    record.state = state.value
    record.file_path = file_path or str(FIXTURE_PDF)
    record.doi = "10.1234/test"
    record.arxiv_id = "2401.00001"
    record.title = "Test Paper on Momentum"
    record.abstract = "An abstract."
    record.published_date = None
    record.journal = None
    record.authors = []
    record.keywords = []
    record.sources = []
    record.relevance_score = 0.8
    record.credibility_score = 0.7
    record.implementability_score = 0.6
    record.reproducibility_score = 0.5
    record.composite_score = 0.65
    record.file_hash = "abc123"
    record.file_size_bytes = 3918
    record.evaluation = {}
    record.created_at = None
    record.updated_at = None
    return record


def _make_repo(record: MagicMock | None) -> MagicMock:
    repo = MagicMock()
    repo.get_by_id.return_value = record
    repo.session = MagicMock()
    repo.transition_state = MagicMock()
    return repo


# ---------------------------------------------------------------------------
# PdfProcessor.process — paper not found
# ---------------------------------------------------------------------------


class TestProcessPaperNotFound:
    def test_returns_failure_when_record_missing(self, tmp_path):
        repo = _make_repo(record=None)
        processor = PdfProcessor(storage_root=tmp_path)
        result = processor.process(uuid.uuid4(), repo)
        assert result.success is False
        assert result.error is not None

    def test_does_not_call_transition_when_missing(self, tmp_path):
        repo = _make_repo(record=None)
        processor = PdfProcessor(storage_root=tmp_path)
        processor.process(uuid.uuid4(), repo)
        repo.transition_state.assert_not_called()


# ---------------------------------------------------------------------------
# PdfProcessor.process — wrong state
# ---------------------------------------------------------------------------


class TestProcessWrongState:
    def test_candidate_state_returns_failure(self, tmp_path):
        pid = uuid.uuid4()
        record = _make_record(pid, state=PaperState.CANDIDATE)
        processor = PdfProcessor(storage_root=tmp_path)
        result = processor.process(pid, _make_repo(record))
        assert result.success is False
        assert "ACQUIRED" in result.error

    def test_discovered_state_returns_failure(self, tmp_path):
        pid = uuid.uuid4()
        record = _make_record(pid, state=PaperState.DISCOVERED)
        processor = PdfProcessor(storage_root=tmp_path)
        result = processor.process(pid, _make_repo(record))
        assert result.success is False


# ---------------------------------------------------------------------------
# PdfProcessor.process — missing file on disk
# ---------------------------------------------------------------------------


class TestProcessMissingFile:
    def test_missing_file_path_returns_failure(self, tmp_path):
        pid = uuid.uuid4()
        record = _make_record(pid, file_path="")
        record.file_path = None
        repo = _make_repo(record)
        processor = PdfProcessor(storage_root=tmp_path)
        result = processor.process(pid, repo)
        assert result.success is False
        assert result.error is not None

    def test_nonexistent_pdf_path_returns_failure(self, tmp_path):
        pid = uuid.uuid4()
        record = _make_record(pid, file_path=str(tmp_path / "ghost.pdf"))
        repo = _make_repo(record)
        processor = PdfProcessor(storage_root=tmp_path)
        result = processor.process(pid, repo)
        assert result.success is False
        assert "not found" in result.error.lower()

    def test_no_state_transition_on_missing_file(self, tmp_path):
        pid = uuid.uuid4()
        record = _make_record(pid, file_path=str(tmp_path / "ghost.pdf"))
        repo = _make_repo(record)
        processor = PdfProcessor(storage_root=tmp_path)
        processor.process(pid, repo)
        repo.transition_state.assert_not_called()


# ---------------------------------------------------------------------------
# PdfProcessor.process — successful run with real fixture PDF
# ---------------------------------------------------------------------------


class TestProcessSuccess:
    def _run(self, tmp_path) -> tuple[ProcessingResult, MagicMock]:
        pid = uuid.uuid4()
        record = _make_record(pid, file_path=str(FIXTURE_PDF))
        repo = _make_repo(record)
        processor = PdfProcessor(storage_root=tmp_path)
        result = processor.process(pid, repo)
        return result, repo

    def test_success_true(self, tmp_path):
        result, _ = self._run(tmp_path)
        assert result.success is True

    def test_final_state_processed(self, tmp_path):
        result, _ = self._run(tmp_path)
        assert result.final_state == PaperState.PROCESSED

    def test_transition_state_called_once(self, tmp_path):
        result, repo = self._run(tmp_path)
        repo.transition_state.assert_called_once()
        args = repo.transition_state.call_args[0]
        assert args[1] == PaperState.PROCESSED

    def test_package_dir_set(self, tmp_path):
        result, _ = self._run(tmp_path)
        assert result.package_dir is not None

    def test_manifest_returned(self, tmp_path):
        result, _ = self._run(tmp_path)
        assert isinstance(result.manifest, PackageManifest)

    def test_extraction_returned(self, tmp_path):
        result, _ = self._run(tmp_path)
        assert isinstance(result.extraction, ExtractionResult)
        assert result.extraction.extraction_failed is False

    def test_section_count_positive(self, tmp_path):
        result, _ = self._run(tmp_path)
        assert result.section_count >= 1

    def test_no_error(self, tmp_path):
        result, _ = self._run(tmp_path)
        assert result.error is None

    def test_package_files_on_disk(self, tmp_path):
        result, _ = self._run(tmp_path)
        for fname in ["metadata.json", "text.txt", "sections.json", "acquisition.json"]:
            assert (result.package_dir / fname).exists(), f"{fname} missing"

    def test_evaluation_updated_on_record(self, tmp_path):
        """processor.py sets record.evaluation['processing'] and flushes."""
        pid = uuid.uuid4()
        record = _make_record(pid, file_path=str(FIXTURE_PDF))
        repo = _make_repo(record)
        processor = PdfProcessor(storage_root=tmp_path)
        processor.process(pid, repo)
        # evaluation dict was mutated to include 'processing' key
        assert "processing" in record.evaluation
        assert record.evaluation["processing"]["processing_version"] == PROCESSING_VERSION

    def test_session_flush_called(self, tmp_path):
        pid = uuid.uuid4()
        record = _make_record(pid, file_path=str(FIXTURE_PDF))
        repo = _make_repo(record)
        processor = PdfProcessor(storage_root=tmp_path)
        processor.process(pid, repo)
        repo.session.flush.assert_called()


# ---------------------------------------------------------------------------
# PdfProcessor.process — PROCESSED state (re-run idempotency)
# ---------------------------------------------------------------------------


class TestProcessAlreadyProcessed:
    def test_reprocessing_allowed(self, tmp_path):
        """Papers in PROCESSED state can be reprocessed (refresh package)."""
        pid = uuid.uuid4()
        record = _make_record(pid, state=PaperState.PROCESSED, file_path=str(FIXTURE_PDF))
        repo = _make_repo(record)
        processor = PdfProcessor(storage_root=tmp_path)
        result = processor.process(pid, repo)
        # Success, but no second state transition (already PROCESSED)
        assert result.success is True
        repo.transition_state.assert_not_called()

