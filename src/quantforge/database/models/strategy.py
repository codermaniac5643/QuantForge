# src/quantforge/database/models/strategy.py
"""
SQLAlchemy ORM model for the strategy_specifications table — Milestone 12.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from quantforge.database.base import Base
from quantforge.strategy.models.enums import StrategyStatus


class StrategyRecord(Base):
    """
    Persisted strategy specification record.
    Maps to the `strategy_specifications` table.

    The full JSON spec is stored on disk at `file_path`.
    This table holds only the metadata needed for queries, status
    management, and reporting — not the extraction content itself.
    """

    __tablename__ = "strategy_specifications"

    # --- Identity ---
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # --- Link to paper ---
    paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("papers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # --- Versioning ---
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )
    variant: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="base",
    )

    # --- Status ---
    status: Mapped[str] = mapped_column(
        SAEnum(
            StrategyStatus,
            values_callable=lambda x: [e.value for e in x],
            name="strategystatus",
            create_type=True,
        ),
        nullable=False,
        default=StrategyStatus.DRAFT.value,
        index=True,
    )

    # --- Classification ---
    completeness_class: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    # --- Artifact path ---
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Provenance ---
    extracted_by: Mapped[str] = mapped_column(String(50), nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )

    # --- Timestamps ---
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "paper_id",
            "version",
            "variant",
            name="uq_strategy_paper_version_variant",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<StrategyRecord id={self.id} "
            f"paper_id={self.paper_id} "
            f"version={self.version} "
            f"variant={self.variant!r} "
            f"status={self.status!r}>"
        )
