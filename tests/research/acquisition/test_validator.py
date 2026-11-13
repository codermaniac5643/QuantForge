# tests/research/acquisition/test_validator.py
"""
Unit tests for PDF validation (validator.py).

Covers:
  - Empty file rejected
  - File missing PDF magic bytes rejected
  - File exceeding max size rejected
  - Valid PDF accepted; hash and size returned
  - Nonexistent file rejected
  - SHA-256 hash computed correctly
"""

import hashlib
import pytest
from pathlib import Path

from quantforge.research.acquisition.validator import validate_pdf, _compute_hash


VALID_PDF_HEADER = b"%PDF-1.4\n%fake pdf content to pass magic byte check\n"
INVALID_HEADER = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50


def write_tmp(tmp_path: Path, content: bytes, name: str = "test.pdf") -> Path:
    p = tmp_path / name
    p.write_bytes(content)
    return p


class TestValidatePdf:
    def test_valid_pdf_accepted(self, tmp_path):
        p = write_tmp(tmp_path, VALID_PDF_HEADER + b"body content")
        result = validate_pdf(p, max_size_mb=10)
        assert result.valid
        assert result.sha256 is not None and len(result.sha256) == 64
        assert result.size_bytes == p.stat().st_size
        assert result.failure_reason is None

    def test_empty_file_rejected(self, tmp_path):
        p = write_tmp(tmp_path, b"")
        result = validate_pdf(p, max_size_mb=10)
        assert not result.valid
        assert "empty" in result.failure_reason.lower()
        assert result.size_bytes == 0

    def test_missing_pdf_magic_rejected(self, tmp_path):
        p = write_tmp(tmp_path, INVALID_HEADER)
        result = validate_pdf(p, max_size_mb=10)
        assert not result.valid
        assert "magic" in result.failure_reason.lower() or "pdf" in result.failure_reason.lower()

    def test_file_too_large_rejected(self, tmp_path):
        content = VALID_PDF_HEADER + b"x" * (1024 * 1024 * 2)
        p = write_tmp(tmp_path, content)
        result = validate_pdf(p, max_size_mb=1)
        assert not result.valid
        assert "limit" in result.failure_reason.lower() or "exceed" in result.failure_reason.lower()
        assert result.size_bytes > 0

    def test_nonexistent_file_rejected(self, tmp_path):
        p = tmp_path / "nonexistent.pdf"
        result = validate_pdf(p, max_size_mb=10)
        assert not result.valid
        assert "does not exist" in result.failure_reason.lower()

    def test_hash_computed_for_valid_pdf(self, tmp_path):
        content = VALID_PDF_HEADER + b"deterministic content"
        p = write_tmp(tmp_path, content)
        result = validate_pdf(p, max_size_mb=10)
        expected = hashlib.sha256(content).hexdigest()
        assert result.sha256 == expected

    def test_hash_computed_even_on_size_failure(self, tmp_path):
        content = VALID_PDF_HEADER + b"x" * (1024 * 1024 * 2)
        p = write_tmp(tmp_path, content)
        result = validate_pdf(p, max_size_mb=1)
        assert not result.valid
        assert result.sha256 is not None

    def test_exactly_at_size_limit_accepted(self, tmp_path):
        max_mb = 1
        content = VALID_PDF_HEADER + b"x" * (max_mb * 1024 * 1024 - len(VALID_PDF_HEADER))
        p = write_tmp(tmp_path, content)
        result = validate_pdf(p, max_size_mb=max_mb)
        assert result.valid

    def test_size_bytes_returned_on_success(self, tmp_path):
        content = VALID_PDF_HEADER + b"body"
        p = write_tmp(tmp_path, content)
        result = validate_pdf(p, max_size_mb=10)
        assert result.size_bytes == len(content)


class TestComputeHash:
    def test_hash_matches_hashlib(self, tmp_path):
        content = b"hello world"
        p = tmp_path / "file.bin"
        p.write_bytes(content)
        expected = hashlib.sha256(content).hexdigest()
        assert _compute_hash(p) == expected

    def test_missing_file_returns_empty_string(self, tmp_path):
        p = tmp_path / "missing.bin"
        assert _compute_hash(p) == ""

