# src/quantforge/research/normalization/__init__.py

from quantforge.research.normalization.text import (
    normalize_title,
    normalize_doi,
    normalize_arxiv_id,
)
from quantforge.research.normalization.deduplication import (
    DeduplicationResult,
    Deduplicator,
)
from quantforge.research.normalization.merger import PaperMerger

__all__ = [
    "normalize_title",
    "normalize_doi",
    "normalize_arxiv_id",
    "DeduplicationResult",
    "Deduplicator",
    "PaperMerger",
]

