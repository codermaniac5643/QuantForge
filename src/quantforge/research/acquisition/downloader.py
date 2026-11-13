# src/quantforge/research/acquisition/downloader.py
"""
Async PDF downloader for QuantForge paper acquisition.

Rules enforced here:
  - Stream large files; never load entire PDF into memory.
  - Use a temp file at the target directory; move atomically to final path.
  - Follow redirects (publisher URLs redirect).
  - Respect per-request timeout from config.
  - Return a DownloadResult — never raise on recoverable errors.
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_UA = "QuantForge/0.1 (research pipeline)"
_CHUNK_SIZE = 65_536  # 64 KB read chunks


@dataclass
class DownloadResult:
    """
    Outcome of a single download attempt.

    Attributes:
        success:         True when the file was written to *dest_path*.
        dest_path:       Final path of the downloaded file (set on success).
        bytes_written:   Total bytes written.
        failure_reason:  Error message when success=False.
    """
    success: bool
    dest_path: Path | None = None
    bytes_written: int = 0
    failure_reason: str | None = None


async def download_pdf(
    client: httpx.AsyncClient,
    url: str,
    dest_path: Path,
    timeout: float = 30.0,
    max_size_bytes: int | None = None,
) -> DownloadResult:
    """
    Download the file at *url* and write it to *dest_path* atomically.

    The file is first streamed to a temp file in the same directory as
    *dest_path*, then renamed into place only after all bytes are written.
    This guarantees *dest_path* never contains a partial download.

    Args:
        client:          Shared httpx.AsyncClient (caller owns lifecycle).
        url:             PDF URL to download.
        dest_path:       Final path where the file should land.
        timeout:         Per-request timeout in seconds.
        max_size_bytes:  Optional hard cap; download aborted if exceeded.

    Returns:
        DownloadResult with success=True when the file is ready at dest_path.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None

    try:
        async with client.stream(
            "GET",
            url,
            timeout=timeout,
            headers={"User-Agent": _UA},
            follow_redirects=True,
        ) as response:
            if response.status_code != 200:
                return DownloadResult(
                    success=False,
                    failure_reason=(
                        f"HTTP {response.status_code} from {url!r}"
                    ),
                )

            # Write to a temp file in the same directory (same filesystem)
            fd, tmp_str = tempfile.mkstemp(
                dir=dest_path.parent,
                prefix=".dl_tmp_",
                suffix=".pdf",
            )
            tmp_path = Path(tmp_str)
            bytes_written = 0

            try:
                with open(fd, "wb") as fh:
                    async for chunk in response.aiter_bytes(chunk_size=_CHUNK_SIZE):
                        bytes_written += len(chunk)
                        if max_size_bytes and bytes_written > max_size_bytes:
                            # Over the cap — abort without keeping temp file
                            fh.close()
                            tmp_path.unlink(missing_ok=True)
                            return DownloadResult(
                                success=False,
                                bytes_written=bytes_written,
                                failure_reason=(
                                    f"Download exceeded size limit "
                                    f"({bytes_written / 1_048_576:.1f} MB)"
                                ),
                            )
                        fh.write(chunk)
            except Exception:
                tmp_path.unlink(missing_ok=True)
                raise

            if bytes_written == 0:
                tmp_path.unlink(missing_ok=True)
                return DownloadResult(
                    success=False,
                    failure_reason=f"Empty response body from {url!r}",
                )

            # Atomic rename to final path
            tmp_path.rename(dest_path)
            logger.info(
                "Downloaded %d bytes → %s", bytes_written, dest_path
            )
            return DownloadResult(
                success=True,
                dest_path=dest_path,
                bytes_written=bytes_written,
            )

    except httpx.TimeoutException as exc:
        if tmp_path:
            tmp_path.unlink(missing_ok=True)
        return DownloadResult(
            success=False,
            failure_reason=f"Timeout downloading {url!r}: {exc}",
        )
    except httpx.HTTPError as exc:
        if tmp_path:
            tmp_path.unlink(missing_ok=True)
        return DownloadResult(
            success=False,
            failure_reason=f"HTTP error downloading {url!r}: {exc}",
        )
    except Exception as exc:
        if tmp_path:
            tmp_path.unlink(missing_ok=True)
        logger.error("Unexpected error downloading %r: %s", url, exc)
        return DownloadResult(
            success=False,
            failure_reason=f"Unexpected error: {exc}",
        )

