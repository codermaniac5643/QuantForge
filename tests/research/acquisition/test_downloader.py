# tests/research/acquisition/test_downloader.py
"""
Unit tests for download_pdf (downloader.py).

All HTTP calls are mocked — no real downloads.
Covers:
  - Successful download writes bytes to dest_path atomically
  - HTTP non-200 returns failure (no file written)
  - Timeout returns failure
  - Size cap exceeded during streaming returns failure
  - Empty response body returns failure
  - Temp file cleaned up on failure
"""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import httpx

from quantforge.research.acquisition.downloader import download_pdf

VALID_PDF_BYTES = b"%PDF-1.4\ncontent here"


def make_streaming_response(content: bytes, status: int = 200):
    """Build a mock async context manager for client.stream()."""
    async def aiter_bytes(chunk_size=65536):
        # Yield in one chunk for simplicity
        yield content

    mock_resp = MagicMock()
    mock_resp.status_code = status
    mock_resp.aiter_bytes = aiter_bytes

    class _CM:
        async def __aenter__(self_inner):
            return mock_resp
        async def __aexit__(self_inner, *a):
            pass

    return _CM()


@pytest.mark.asyncio
class TestDownloadPdf:
    async def test_successful_download(self, tmp_path):
        dest = tmp_path / "paper.pdf"
        client = MagicMock(spec=httpx.AsyncClient)
        client.stream.return_value = make_streaming_response(VALID_PDF_BYTES)

        result = await download_pdf(client, "https://example.com/paper.pdf", dest, timeout=10)

        assert result.success
        assert result.dest_path == dest
        assert dest.exists()
        assert dest.read_bytes() == VALID_PDF_BYTES
        assert result.bytes_written == len(VALID_PDF_BYTES)

    async def test_http_non_200_returns_failure(self, tmp_path):
        dest = tmp_path / "paper.pdf"
        client = MagicMock(spec=httpx.AsyncClient)
        client.stream.return_value = make_streaming_response(b"error body", status=404)

        result = await download_pdf(client, "https://example.com/paper.pdf", dest)

        assert not result.success
        assert "404" in result.failure_reason
        assert not dest.exists()

    async def test_timeout_returns_failure(self, tmp_path):
        dest = tmp_path / "paper.pdf"
        client = MagicMock(spec=httpx.AsyncClient)
        client.stream.side_effect = httpx.TimeoutException("timed out")

        result = await download_pdf(client, "https://example.com/paper.pdf", dest)

        assert not result.success
        assert "timeout" in result.failure_reason.lower()
        assert not dest.exists()

    async def test_size_cap_exceeded_returns_failure(self, tmp_path):
        dest = tmp_path / "paper.pdf"
        big_content = b"%PDF" + b"x" * 1024  # 1 KB
        client = MagicMock(spec=httpx.AsyncClient)
        client.stream.return_value = make_streaming_response(big_content)

        # Cap at 100 bytes — will be exceeded
        result = await download_pdf(
            client, "https://example.com/paper.pdf", dest,
            max_size_bytes=100,
        )

        assert not result.success
        assert "size" in result.failure_reason.lower() or "limit" in result.failure_reason.lower()
        assert not dest.exists()

    async def test_empty_response_returns_failure(self, tmp_path):
        dest = tmp_path / "paper.pdf"
        client = MagicMock(spec=httpx.AsyncClient)
        client.stream.return_value = make_streaming_response(b"")

        result = await download_pdf(client, "https://example.com/paper.pdf", dest)

        assert not result.success
        assert "empty" in result.failure_reason.lower()

    async def test_no_temp_file_left_on_timeout(self, tmp_path):
        dest = tmp_path / "paper.pdf"
        client = MagicMock(spec=httpx.AsyncClient)
        client.stream.side_effect = httpx.TimeoutException("timed out")

        await download_pdf(client, "https://example.com/paper.pdf", dest)

        # No .dl_tmp_ files should remain
        leftovers = list(tmp_path.glob(".dl_tmp_*"))
        assert leftovers == []

    async def test_parent_dirs_created(self, tmp_path):
        dest = tmp_path / "subdir" / "nested" / "paper.pdf"
        client = MagicMock(spec=httpx.AsyncClient)
        client.stream.return_value = make_streaming_response(VALID_PDF_BYTES)

        result = await download_pdf(client, "https://example.com/paper.pdf", dest)

        assert result.success
        assert dest.exists()

    async def test_http_error_returns_failure(self, tmp_path):
        dest = tmp_path / "paper.pdf"
        client = MagicMock(spec=httpx.AsyncClient)
        client.stream.side_effect = httpx.HTTPError("connection reset")

        result = await download_pdf(client, "https://example.com/paper.pdf", dest)

        assert not result.success
        assert not dest.exists()

