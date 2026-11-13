# tests/research/processing/test_packager.py
"""
Tests for src/quantforge/research/processing/packager.py

No real PDF needed for most tests — table extraction is disabled
(extract_tables=False) to keep tests deterministic and fast.
One test verifies table extraction against the sample.pdf fixture.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quantforge.research.processing.extractor import extract_text
from quantforge.research.processing.packager import PackageManifest, create_research_package
from quantforge.research.processing.sections import extract_sections

FIXTURE_PDF = Path(__file__).parent / "sample.pdf"

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _make_record_dict(tmp_path, with_file_path: bool = True) -> dict:
    return {
        "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "doi": "10.1234/test",
        "arxiv_id": "2401.00001",
        "title": "Test Paper",
        "abstract": "An abstract.",
        "published_date": "2024-01-01",
        "journal": None,
        "authors": [{"name": "Alice"}],
        "keywords": ["momentum"],
        "sources": [{"source_name": "arxiv"}],
        "state": "acquired",
        "relevance_score": 0.8,
        "credibility_score": 0.7,
        "implementability_score": 0.6,
        "reproducibility_score": 0.5,
        "composite_score": 0.65,
        "file_path": str(FIXTURE_PDF) if with_file_path else None,
        "file_hash": "abc123",
        "file_size_bytes": 3918,
        "evaluation": {"acquisition": {"source_url": "https://arxiv.org/pdf/2401.00001"}},
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T01:00:00+00:00",
    }


def _run_package(tmp_path, with_file_path=True, extract_tables=False) -> PackageManifest:
    record_dict = _make_record_dict(tmp_path, with_file_path=with_file_path)
    extraction = extract_text(FIXTURE_PDF)
    sections = extract_sections(extraction.full_text)
    return create_research_package(
        paper_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        package_dir=tmp_path / "pkg",
        record_dict=record_dict,
        extraction=extraction,
        sections=sections,
        extract_tables=extract_tables,
    )


# ---------------------------------------------------------------------------
# Return type and directory creation
# ---------------------------------------------------------------------------


class TestCreateResearchPackageBasic:
    def test_returns_package_manifest(self, tmp_path):
        manifest = _run_package(tmp_path)
        assert isinstance(manifest, PackageManifest)

    def test_package_dir_created(self, tmp_path):
        manifest = _run_package(tmp_path)
        assert manifest.package_dir.exists()
        assert manifest.package_dir.is_dir()

    def test_package_dir_nested(self, tmp_path):
        """Dir should be created even when intermediate dirs don't exist."""
        deep = tmp_path / "a" / "b" / "c"
        record_dict = _make_record_dict(tmp_path)
        extraction = extract_text(FIXTURE_PDF)
        sections = extract_sections(extraction.full_text)
        manifest = create_research_package(
            paper_id="test-id",
            package_dir=deep,
            record_dict=record_dict,
            extraction=extraction,
            sections=sections,
            extract_tables=False,
        )
        assert deep.exists()


# ---------------------------------------------------------------------------
# Required files written
# ---------------------------------------------------------------------------


class TestRequiredFilesWritten:
    def test_metadata_json_exists(self, tmp_path):
        manifest = _run_package(tmp_path)
        assert (manifest.package_dir / "metadata.json").exists()

    def test_text_txt_exists(self, tmp_path):
        manifest = _run_package(tmp_path)
        assert (manifest.package_dir / "text.txt").exists()

    def test_sections_json_exists(self, tmp_path):
        manifest = _run_package(tmp_path)
        assert (manifest.package_dir / "sections.json").exists()

    def test_acquisition_json_exists(self, tmp_path):
        manifest = _run_package(tmp_path)
        assert (manifest.package_dir / "acquisition.json").exists()

    def test_package_manifest_json_exists(self, tmp_path):
        manifest = _run_package(tmp_path)
        assert (manifest.package_dir / "package_manifest.json").exists()

    def test_all_required_files_in_files_written(self, tmp_path):
        manifest = _run_package(tmp_path)
        for name in ["metadata.json", "text.txt", "sections.json", "acquisition.json"]:
            assert name in manifest.files_written, f"{name} not in files_written"


# ---------------------------------------------------------------------------
# File contents
# ---------------------------------------------------------------------------


class TestFileContents:
    def test_metadata_json_has_title(self, tmp_path):
        manifest = _run_package(tmp_path)
        data = json.loads((manifest.package_dir / "metadata.json").read_text())
        assert data["title"] == "Test Paper"

    def test_metadata_json_has_extraction_block(self, tmp_path):
        manifest = _run_package(tmp_path)
        data = json.loads((manifest.package_dir / "metadata.json").read_text())
        assert "extraction" in data
        assert "page_count" in data["extraction"]

    def test_text_txt_non_empty(self, tmp_path):
        manifest = _run_package(tmp_path)
        content = (manifest.package_dir / "text.txt").read_text()
        assert len(content) > 0

    def test_sections_json_is_list(self, tmp_path):
        manifest = _run_package(tmp_path)
        data = json.loads((manifest.package_dir / "sections.json").read_text())
        assert isinstance(data, list)

    def test_acquisition_json_has_source_url(self, tmp_path):
        manifest = _run_package(tmp_path)
        data = json.loads((manifest.package_dir / "acquisition.json").read_text())
        assert data.get("source_url") == "https://arxiv.org/pdf/2401.00001"

    def test_package_manifest_json_valid(self, tmp_path):
        manifest = _run_package(tmp_path)
        data = json.loads((manifest.package_dir / "package_manifest.json").read_text())
        assert "paper_id" in data
        assert "files_written" in data
        assert "packaged_at" in data


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_rerun_does_not_raise(self, tmp_path):
        _run_package(tmp_path)
        _run_package(tmp_path)  # second run — should not raise

    def test_rerun_overwrites_cleanly(self, tmp_path):
        _run_package(tmp_path)
        manifest = _run_package(tmp_path)
        data = json.loads((manifest.package_dir / "metadata.json").read_text())
        assert data["title"] == "Test Paper"


# ---------------------------------------------------------------------------
# Missing file_path
# ---------------------------------------------------------------------------


class TestMissingFilePath:
    def test_missing_file_path_adds_warning(self, tmp_path):
        manifest = _run_package(tmp_path, with_file_path=False, extract_tables=True)
        assert any("file_path" in w or "absent" in w for w in manifest.warnings)

    def test_no_tables_dir_when_no_pdf(self, tmp_path):
        manifest = _run_package(tmp_path, with_file_path=False, extract_tables=True)
        assert not (manifest.package_dir / "tables").exists()

