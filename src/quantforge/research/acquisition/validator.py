# src/quantforge/research/acquisition/validator.py
"""
PDF validation for QuantForge paper acquisition.

Validation is applied to every downloaded file *before* it is moved
to its final storage path. If validation fails the temp file is
discarded and the paper remains in APPROVED state for a retry.

Checks performed (in order):
  1. File is not empty.
  2. File starts with the PDF magic bytes (%PDF).
  3. File size does not exceed the configured maximum.
  4. SHA-256 hash is computed and returned (always — even on failure).
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


class ValidationError(Exception):
    """
    Raised when a downloaded file fails PDF validation.

    Used by the retry layer (retry.py) to classify validation failures
    as permanent errors — retrying a corrupted or non-PDF file will
    not produce a different outcome.
    """


# PDF magic bytes — every valid PDF starts with these
_PDF_MAGIC = b"%PDF"
# How many bytes to read when checking the magic bytes
_MAGIC_CHECK_BYTES = 8


@dataclass
class ValidationResult:
    """
    Outcome of validating a downloaded PDF file.

    Attributes:
        valid:          True if all checks passed.
        sha256:         Hex-encoded SHA-256 hash of the file (always set when
                        the file exists, even on failure).
        size_bytes:     File size in bytes (0 if file absent).
        failure_reason: Human-readable reason when valid=False.
    """
    valid: bool
    sha256: str | None = None
    size_bytes: int = 0
    failure_reason: str | None = None


def validate_pdf(path: Path, max_size_mb: int) -> ValidationResult:
    """
    Validate a downloaded file at *path* as a well-formed PDF.

    Args:
        path:        Path to the downloaded file (temp location).
        max_size_mb: Maximum allowed file size in megabytes.

    Returns:
        ValidationResult with valid=True when all checks pass.
    """
    if not path.exists():
        return ValidationResult(
            valid=False,
            failure_reason=f"File does not exist: {path}",
        )

    size_bytes = path.stat().st_size

    # 1. Empty file check
    if size_bytes == 0:
        return ValidationResult(
            valid=False,
            size_bytes=0,
            failure_reason="Downloaded file is empty (0 bytes)",
        )

    # 2. Size cap
    max_bytes = max_size_mb * 1024 * 1024
    if size_bytes > max_bytes:
        sha = _compute_hash(path)
        return ValidationResult(
            valid=False,
            sha256=sha,
            size_bytes=size_bytes,
            failure_reason=(
                f"File size {size_bytes / 1_048_576:.1f} MB exceeds "
                f"limit of {max_size_mb} MB"
            ),
        )

    # 3. PDF magic bytes
    try:
        with path.open("rb") as fh:
            header = fh.read(_MAGIC_CHECK_BYTES)
    except OSError as exc:
        return ValidationResult(
            valid=False,
            size_bytes=size_bytes,
            failure_reason=f"Could not read file: {exc}",
        )

    if not header.startswith(_PDF_MAGIC):
        sha = _compute_hash(path)
        return ValidationResult(
            valid=False,
            sha256=sha,
            size_bytes=size_bytes,
            failure_reason=(
                f"File does not start with PDF magic bytes "
                f"(got {header[:8]!r})"
            ),
        )

    # 4. Compute hash (last — avoids reading the whole file unless we pass)
    sha = _compute_hash(path)
    logger.debug(
        "PDF validated: size=%d bytes sha256=%s path=%s",
        size_bytes, sha, path,
    )
    return ValidationResult(
        valid=True,
        sha256=sha,
        size_bytes=size_bytes,
    )


def _compute_hash(path: Path) -> str:
    """Return the hex-encoded SHA-256 hash of the file at *path*."""
    h = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65_536), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()

