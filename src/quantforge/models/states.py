# src/quantforge/models/states.py

from enum import Enum


class PaperState(str, Enum):
    """
    Lifecycle states for a research paper in the QuantForge pipeline.
    A paper moves forward through these states as it is processed.
    Failures are recorded as separate events, not separate states,
    so a paper can be retried from its last successful state.
    """

    # Paper metadata discovered from a source (arXiv, OpenAlex, etc.)
    DISCOVERED = "discovered"

    # Metadata normalized and deduplicated against existing records
    NORMALIZED = "normalized"

    # Scored across relevance, credibility, implementability, reproducibility
    EVALUATED = "evaluated"

    # Passed score thresholds — eligible for acquisition
    CANDIDATE = "candidate"

    # Approved for download (manually or automatically per config)
    APPROVED = "approved"

    # PDF successfully downloaded and validated
    ACQUIRED = "acquired"

    # Text and sections extracted — Research Document Package created
    PROCESSED = "processed"

    # Explicitly rejected at any stage — will not be retried
    REJECTED = "rejected"


# Legal forward transitions — a paper can only move in these directions
ALLOWED_TRANSITIONS: dict[PaperState, set[PaperState]] = {
    PaperState.DISCOVERED:  {PaperState.NORMALIZED, PaperState.REJECTED},
    PaperState.NORMALIZED:  {PaperState.EVALUATED, PaperState.REJECTED},
    PaperState.EVALUATED:   {PaperState.CANDIDATE, PaperState.REJECTED},
    PaperState.CANDIDATE:   {PaperState.APPROVED, PaperState.REJECTED},
    PaperState.APPROVED:    {PaperState.ACQUIRED, PaperState.REJECTED},
    PaperState.ACQUIRED:    {PaperState.PROCESSED, PaperState.REJECTED},
    PaperState.PROCESSED:   set(),   # terminal success state
    PaperState.REJECTED:    set(),   # terminal failure state
}


def is_valid_transition(from_state: PaperState, to_state: PaperState) -> bool:
    """Returns True if moving from from_state to to_state is a legal transition."""
    return to_state in ALLOWED_TRANSITIONS.get(from_state, set())


def is_terminal(state: PaperState) -> bool:
    """Returns True if the state is a terminal state (no further transitions possible)."""
    return not ALLOWED_TRANSITIONS.get(state)