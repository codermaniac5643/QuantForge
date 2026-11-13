# src/quantforge/models/paper.py

from datetime import date, datetime, timezone
from uuid import UUID, uuid4
from pydantic import BaseModel, Field, HttpUrl
from quantforge.models.states import PaperState


class SourceReference(BaseModel):
    source_name: str
    source_id: str
    source_url: HttpUrl | None = None
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    raw_metadata: dict = Field(default_factory=dict)

class Author(BaseModel):
    name: str
    affiliation: str | None = None
    orcid: str | None = None


class ResearchPaper(BaseModel):
    """
    Canonical representation of a research paper.
    Populated from normalized source metadata.
    Fields left None when not available — never fabricated.
    """

    # --- Identity ---
    id: UUID = Field(default_factory=uuid4)
    doi: str | None = None
    arxiv_id: str | None = None

    # --- Bibliographic Metadata ---
    title: str
    authors: list[Author] = Field(default_factory=list)
    abstract: str | None = None
    published_date: date | None = None
    journal: str | None = None
    keywords: list[str] = Field(default_factory=list)

    # --- Source Provenance ---
    # All sources where this paper was discovered
    sources: list[SourceReference] = Field(default_factory=list)

    # --- Pipeline State ---
    state: PaperState = PaperState.DISCOVERED
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # --- Acquisition ---
    # Populated after successful download
    file_path: str | None = None
    file_hash: str | None = None
    file_size_bytes: int | None = None

    def add_source(self, source: SourceReference) -> None:
        """Add a new source reference if not already present."""
        existing_ids = {s.source_id for s in self.sources}
        if source.source_id not in existing_ids:
            self.sources.append(source)

    def has_source(self, source_name: str) -> bool:
        """Check if this paper was discovered from a given source."""
        return any(s.source_name == source_name for s in self.sources)