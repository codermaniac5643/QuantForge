# src/quantforge/database/models/paper.py

import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    String, Text, Date, DateTime, Integer, Float,
    Boolean, JSON, Enum as SAEnum, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from quantforge.database.base import Base
from quantforge.models.states import PaperState


class PaperRecord(Base):
    """
    Persisted research paper record.
    Maps to the `papers` table in PostgreSQL.
    """
    __tablename__ = "papers"

    # --- Identity ---
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    doi: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    arxiv_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)

    # --- Bibliographic Metadata ---
    title: Mapped[str] = mapped_column(Text, nullable=False)
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    journal: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # --- JSON fields (lists stored as JSON) ---
    # e.g. [{"name": "Jane Smith", "affiliation": "MIT"}]
    authors: Mapped[list] = mapped_column(JSON, default=list)
    keywords: Mapped[list] = mapped_column(JSON, default=list)

    # --- Source Provenance ---
    # All source references preserved as JSON
    # e.g. [{"source_name": "arxiv", "source_id": "2401.00001", ...}]
    sources: Mapped[list] = mapped_column(JSON, default=list)

    # --- Evaluation Scores ---
    relevance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    credibility_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    implementability_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    reproducibility_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    priority_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    composite_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Full evaluation stored as JSON for traceability
    evaluation: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # --- Pipeline State ---
    state: Mapped[str] = mapped_column(
        SAEnum(PaperState, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=PaperState.DISCOVERED.value,
        index=True,
    )

    # --- Acquisition ---
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- Timestamps ---
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        # Prevent duplicate papers by DOI or arXiv ID
        UniqueConstraint("doi", name="uq_papers_doi"),
        UniqueConstraint("arxiv_id", name="uq_papers_arxiv_id"),
    )

    def __repr__(self) -> str:
        return f"<PaperRecord id={self.id} state={self.state} title={self.title[:50]!r}>"