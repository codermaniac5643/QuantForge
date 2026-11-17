# src/quantforge/database/repositories/strategy.py
"""
Repository layer for strategy_specifications — Milestone 12.

Follows the same session-injection pattern as PaperRepository:
  - Accepts a SQLAlchemy Session at __init__; never creates its own.
  - Caller manages commit/rollback via get_session().
  - Uses session.flush() to assign IDs without committing.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from quantforge.database.models.strategy import StrategyRecord
from quantforge.strategy.models.enums import (
    StrategyStatus,
    is_valid_status_transition,
)


class DuplicateStrategyError(Exception):
    """Raised when (paper_id, version, variant) already exists."""


class InvalidStatusTransitionError(Exception):
    """Raised when a status transition is not permitted."""


class StrategyRepository:
    """
    All database operations for strategy specifications.
    Session lifecycle is managed by the caller.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def create(self, record: StrategyRecord) -> StrategyRecord:
        """
        Persist a new StrategyRecord.
        Raises DuplicateStrategyError on (paper_id, version, variant)
        uniqueness violation.
        """
        self.session.add(record)
        try:
            self.session.flush()
        except IntegrityError as exc:
            self.session.rollback()
            raise DuplicateStrategyError(
                f"Strategy spec already exists for paper_id={record.paper_id} "
                f"version={record.version} variant={record.variant!r}"
            ) from exc
        return record

    def update_status(
        self,
        strategy_id: UUID,
        new_status: StrategyStatus,
        reviewer_id: str | None = None,
    ) -> StrategyRecord:
        """
        Transition a strategy to a new status.
        Validates against ALLOWED_STATUS_TRANSITIONS.
        Raises InvalidStatusTransitionError for bad transitions.
        Raises ValueError if the record does not exist.
        """
        record = self.get_by_id(strategy_id)
        if record is None:
            raise ValueError(f"StrategyRecord {strategy_id} not found")

        from_status = StrategyStatus(record.status)
        if not is_valid_status_transition(from_status, new_status):
            raise InvalidStatusTransitionError(
                f"Cannot transition strategy {strategy_id} "
                f"from {from_status.value!r} to {new_status.value!r}"
            )

        record.status = new_status.value
        if reviewer_id is not None:
            record.reviewed_by = reviewer_id
        self.session.flush()
        return record

    def supersede_previous(
        self,
        paper_id: UUID,
        variant: str,
        keep_id: UUID,
    ) -> None:
        """
        Mark all base/variant records for this paper+variant as SUPERSEDED,
        except the one identified by keep_id.

        Used when a new version replaces earlier extractions.
        Only records currently in APPROVED or DRAFT state are superseded
        (REJECTED and already-SUPERSEDED records are left as-is).
        """
        stmt = select(StrategyRecord).where(
            StrategyRecord.paper_id == paper_id,
            StrategyRecord.variant == variant,
            StrategyRecord.id != keep_id,
            StrategyRecord.status.in_(
                [StrategyStatus.APPROVED.value, StrategyStatus.DRAFT.value,
                 StrategyStatus.UNDER_REVIEW.value]
            ),
        )
        rows = list(self.session.scalars(stmt))
        for row in rows:
            row.status = StrategyStatus.SUPERSEDED.value
        if rows:
            self.session.flush()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_by_id(self, strategy_id: UUID) -> StrategyRecord | None:
        """Fetch a strategy record by its UUID."""
        return self.session.get(StrategyRecord, strategy_id)

    def get_by_paper_id(self, paper_id: UUID) -> list[StrategyRecord]:
        """Return all strategy records for a given paper, oldest first."""
        stmt = (
            select(StrategyRecord)
            .where(StrategyRecord.paper_id == paper_id)
            .order_by(StrategyRecord.version.asc(), StrategyRecord.created_at.asc())
        )
        return list(self.session.scalars(stmt))

    def get_latest_base(self, paper_id: UUID) -> StrategyRecord | None:
        """
        Return the highest-version base spec for a paper that is not
        SUPERSEDED or REJECTED, or None if none exists.
        """
        stmt = (
            select(StrategyRecord)
            .where(
                StrategyRecord.paper_id == paper_id,
                StrategyRecord.variant == "base",
                StrategyRecord.status.notin_(
                    [StrategyStatus.SUPERSEDED.value, StrategyStatus.REJECTED.value]
                ),
            )
            .order_by(StrategyRecord.version.desc())
            .limit(1)
        )
        return self.session.scalar(stmt)

    def list_by_status(self, status: StrategyStatus) -> list[StrategyRecord]:
        """Return all strategy records with the given status."""
        stmt = (
            select(StrategyRecord)
            .where(StrategyRecord.status == status.value)
            .order_by(StrategyRecord.created_at.asc())
        )
        return list(self.session.scalars(stmt))

    def list_by_paper(self, paper_id: UUID) -> list[StrategyRecord]:
        """Alias for get_by_paper_id — clearer name for CLI callers."""
        return self.get_by_paper_id(paper_id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def next_version(self, paper_id: UUID, variant: str = "base") -> int:
        """
        Return the next version number for a (paper_id, variant) pair.
        Returns 1 if no record exists yet.
        """
        from sqlalchemy import func

        stmt = select(func.max(StrategyRecord.version)).where(
            StrategyRecord.paper_id == paper_id,
            StrategyRecord.variant == variant,
        )
        max_ver: int | None = self.session.scalar(stmt)
        return (max_ver or 0) + 1
