# src/quantforge/research/normalization/text.py
"""
Deterministic text normalization utilities for research paper metadata.

These functions are used to produce stable comparison keys for
deduplication. They do NOT modify the canonical paper record —
they only produce lookup keys.
"""

import re
import unicodedata


# Characters to strip when normalizing a title for comparison
_PUNCTUATION_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")

# arXiv URL prefixes we want to strip
_ARXIV_URL_PREFIXES = (
    "https://arxiv.org/abs/",
    "http://arxiv.org/abs/",
    "arxiv.org/abs/",
)

# arXiv version suffix pattern: e.g. "v1", "v12"
_ARXIV_VERSION_RE = re.compile(r"v\d+$", re.IGNORECASE)

# DOI URL prefixes
_DOI_URL_PREFIXES = (
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
    "doi:",
)


def normalize_title(title: str) -> str:
    """
    Produce a stable comparison key from a paper title.

    Steps:
    1. Unicode NFC normalization (compose canonical forms).
    2. Lowercase.
    3. Strip punctuation.
    4. Collapse all whitespace.
    5. Strip leading/trailing whitespace.

    The result is used only for deduplication comparison — it is
    never stored as the paper's canonical title.
    """
    if not title:
        return ""
    # 1. Unicode NFC
    normalized = unicodedata.normalize("NFC", title)
    # 2. Lowercase
    normalized = normalized.lower()
    # 3. Remove punctuation (keep word chars and spaces)
    normalized = _PUNCTUATION_RE.sub(" ", normalized)
    # 4. Collapse whitespace
    normalized = _WHITESPACE_RE.sub(" ", normalized)
    return normalized.strip()


def normalize_doi(doi: str | None) -> str | None:
    """
    Produce a stable comparison key from a DOI.

    - Strips common URL prefixes (https://doi.org/, doi:, etc.).
    - Lowercases the entire string.
    - Strips leading/trailing whitespace.

    Returns None if the input is None or empty after stripping.
    """
    if not doi:
        return None
    cleaned = doi.strip()
    lower = cleaned.lower()
    for prefix in _DOI_URL_PREFIXES:
        if lower.startswith(prefix):
            cleaned = cleaned[len(prefix):]
            break
    cleaned = cleaned.strip()
    return cleaned.lower() if cleaned else None


def normalize_arxiv_id(arxiv_id: str | None) -> str | None:
    """
    Produce a stable comparison key from an arXiv identifier.

    - Strips URL prefixes (https://arxiv.org/abs/, etc.).
    - Strips version suffix (v1, v2, ...).
    - Strips leading/trailing whitespace.
    - Lowercases.

    Returns None if the input is None or empty after stripping.

    Examples:
        "https://arxiv.org/abs/2401.00001v1" → "2401.00001"
        "2401.00001V2"                       → "2401.00001"
        "2401.00001"                         → "2401.00001"
    """
    if not arxiv_id:
        return None
    cleaned = arxiv_id.strip()
    # Strip URL prefix
    for prefix in _ARXIV_URL_PREFIXES:
        if cleaned.lower().startswith(prefix):
            cleaned = cleaned[len(prefix):]
            break
    # Strip version suffix
    cleaned = _ARXIV_VERSION_RE.sub("", cleaned).strip()
    return cleaned.lower() if cleaned else None

