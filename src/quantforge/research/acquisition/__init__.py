# src/quantforge/research/acquisition/__init__.py

from quantforge.research.acquisition.validator import ValidationResult, validate_pdf
from quantforge.research.acquisition.resolver import ResolvedUrl, PdfResolver
from quantforge.research.acquisition.downloader import DownloadResult, download_pdf
from quantforge.research.acquisition.manager import (
    AcquisitionOutcome,
    AcquisitionSummary,
    AcquisitionManager,
)

__all__ = [
    "ValidationResult",
    "validate_pdf",
    "ResolvedUrl",
    "PdfResolver",
    "DownloadResult",
    "download_pdf",
    "AcquisitionOutcome",
    "AcquisitionSummary",
    "AcquisitionManager",
]

