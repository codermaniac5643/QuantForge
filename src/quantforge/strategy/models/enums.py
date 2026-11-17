# src/quantforge/strategy/models/enums.py
"""
Strategy Engineering enumerations — Milestone 12.

All enums are str-based so they serialise cleanly to JSON and PostgreSQL.
"""

from __future__ import annotations

from enum import Enum


class StrategyStatus(str, Enum):
    """Lifecycle status of a Strategy Specification."""

    DRAFT = "draft"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class CompletenessClass(str, Enum):
    """High-level classification of what a paper covers."""

    COMPLETE_STRATEGY = "complete_strategy"
    ENTRY_FOCUSED = "entry_focused"
    EXIT_FOCUSED = "exit_focused"
    SIZING_FOCUSED = "sizing_focused"
    RISK_FOCUSED = "risk_focused"
    REGIME_FOCUSED = "regime_focused"
    PORTFOLIO_FOCUSED = "portfolio_focused"
    RESEARCH_ONLY = "research_only"


class ProvenanceLabel(str, Enum):
    """Provenance of a rule extracted from a paper."""

    EXPLICIT = "explicit"
    INFERRED = "inferred"
    QUANTFORGE_ADDED = "quantforge_added"


class ComponentCoverage(str, Enum):
    """Coverage level for a single pillar."""

    PRESENT = "present"
    PARTIAL = "partial"
    ABSENT = "absent"


class MissingImpact(str, Enum):
    """Severity of a missing component."""

    CRITICAL = "critical"
    MODERATE = "moderate"
    MINOR = "minor"


class MissingAction(str, Enum):
    """Recommended action for a missing component."""

    IGNORE = "ignore"
    REQUIRES_HUMAN_DECISION = "requires_human_decision"
    BLOCKS_IMPLEMENTATION = "blocks_implementation"


class Implementability(str, Enum):
    """Overall implementability of the strategy."""

    DIRECTLY_IMPLEMENTABLE = "directly_implementable"
    REQUIRES_ADDITIONS = "requires_additions"
    RESEARCH_ONLY = "research_only"


class ExtractionMethod(str, Enum):
    """How the extraction was performed."""

    LLM_ASSISTED = "llm_assisted"
    MANUAL = "manual"
    HYBRID = "hybrid"


# Valid status transitions for Strategy Specifications
ALLOWED_STATUS_TRANSITIONS: dict[StrategyStatus, set[StrategyStatus]] = {
    StrategyStatus.DRAFT: {StrategyStatus.UNDER_REVIEW, StrategyStatus.REJECTED},
    StrategyStatus.UNDER_REVIEW: {
        StrategyStatus.APPROVED,
        StrategyStatus.REJECTED,
    },
    StrategyStatus.APPROVED: {StrategyStatus.SUPERSEDED},
    StrategyStatus.REJECTED: {StrategyStatus.DRAFT},      # allow re-extraction
    StrategyStatus.SUPERSEDED: set(),                      # terminal
}


def is_valid_status_transition(
    from_status: StrategyStatus, to_status: StrategyStatus
) -> bool:
    return to_status in ALLOWED_STATUS_TRANSITIONS.get(from_status, set())
