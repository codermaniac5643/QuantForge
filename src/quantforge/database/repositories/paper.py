# src/quantforge/database/repositories/paper.py

from uuid import UUID
from sqlalchemy.orm import Session
from sqlalchemy import select

from quantforge.database.models.paper import PaperRecord
from quantforge.models.states import PaperState, is_valid_transition


class DuplicatePaperError(Exception):
    """Raised when a paper with the same DOI or arXiv ID already exists."""
    pass


class InvalidStateTransitionError(Exception):
    """Raised when a state transition is not allowed."""
    pass


class PaperRepository:
    """
    All database operations for research papers.
    Accepts a SQLAlchemy session — does not create its own.
    Session lifecycle (commit/rollback) is managed by the caller via get_session().
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, record: PaperRecord) -> PaperRecord:
        """
        Persist a new paper record.
        Raises DuplicatePaperError if DOI or arXiv ID already exists.
        """
        if record.doi:
            existing = self.get_by_doi(record.doi)
            if existing:
                raise DuplicatePaperError(
                    f"Paper with DOI {record.doi!r} already exists (id={existing.id})"
                )
        if record.arxiv_id:
            existing = self.get_by_arxiv_id(record.arxiv_id)
            if existing:
                raise DuplicatePaperError(
                    f"Paper with arXiv ID {record.arxiv_id!r} already exists (id={existing.id})"
                )
        self.session.add(record)
        self.session.flush()  # assigns id without committing
        return record

    def get_by_id(self, paper_id: UUID) -> PaperRecord | None:
        """Fetch a paper by its internal UUID."""
        return self.session.get(PaperRecord, paper_id)

    def get_by_doi(self, doi: str) -> PaperRecord | None:
        """Fetch a paper by DOI. Returns None if not found."""
        stmt = select(PaperRecord).where(PaperRecord.doi == doi)
        return self.session.scalar(stmt)

    def get_by_arxiv_id(self, arxiv_id: str) -> PaperRecord | None:
        """Fetch a paper by arXiv ID. Returns None if not found."""
        stmt = select(PaperRecord).where(PaperRecord.arxiv_id == arxiv_id)
        return self.session.scalar(stmt)

    def get_by_state(self, state: PaperState) -> list[PaperRecord]:
        """Fetch all papers currently in a given state."""
        stmt = (
            select(PaperRecord)
            .where(PaperRecord.state == state.value)
            .order_by(PaperRecord.created_at.asc())
        )
        return list(self.session.scalars(stmt))

    def transition_state(
        self,
        paper_id: UUID,
        to_state: PaperState,
    ) -> PaperRecord:
        """
        Move a paper to a new state.
        Validates the transition against the state machine before applying.
        Raises InvalidStateTransitionError if the transition is not allowed.
        Raises ValueError if the paper does not exist.
        """
        record = self.get_by_id(paper_id)
        if record is None:
            raise ValueError(f"Paper {paper_id} not found")

        from_state = PaperState(record.state)
        if not is_valid_transition(from_state, to_state):
            raise InvalidStateTransitionError(
                f"Cannot transition paper {paper_id} "
                f"from {from_state.value!r} to {to_state.value!r}"
            )

        record.state = to_state.value
        self.session.flush()
        return record

    def update_scores(
        self,
        paper_id: UUID,
        relevance: float | None = None,
        credibility: float | None = None,
        implementability: float | None = None,
        reproducibility: float | None = None,
        priority: float | None = None,
        composite: float | None = None,
        evaluation: dict | None = None,
    ) -> PaperRecord:
        """Update evaluation scores on a paper record."""
        record = self.get_by_id(paper_id)
        if record is None:
            raise ValueError(f"Paper {paper_id} not found")

        if relevance is not None:
            record.relevance_score = relevance
        if credibility is not None:
            record.credibility_score = credibility
        if implementability is not None:
            record.implementability_score = implementability
        if reproducibility is not None:
            record.reproducibility_score = reproducibility
        if priority is not None:
            record.priority_score = priority
        if composite is not None:
            record.composite_score = composite
        if evaluation is not None:
            record.evaluation = evaluation

        self.session.flush()
        return record

    def count_by_state(self) -> dict[str, int]:
        """Return a count of papers grouped by state. Useful for status reports."""
        from sqlalchemy import func
        stmt = (
            select(PaperRecord.state, func.count().label("count"))
            .group_by(PaperRecord.state)
        )
        rows = self.session.execute(stmt).all()
        return {row.state: row.count for row in rows}