# src/quantforge/research/processing/__init__.py
"""
PDF processing and Research Document Package creation — Milestone 8.

Converts acquired PDFs into usable text, sections, and a structured
Research Document Package on disk.
"""

from quantforge.research.processing.extractor import extract_text, ExtractionResult
from quantforge.research.processing.sections import extract_sections, SectionEntry
from quantforge.research.processing.packager import create_research_package, PackageManifest
from quantforge.research.processing.processor import PdfProcessor, ProcessingResult

__all__ = [
    "extract_text",
    "ExtractionResult",
    "extract_sections",
    "SectionEntry",
    "create_research_package",
    "PackageManifest",
    "PdfProcessor",
    "ProcessingResult",
]

