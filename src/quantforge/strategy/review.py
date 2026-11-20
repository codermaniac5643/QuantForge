# src/quantforge/strategy/review.py
"""
Review service — Milestone 15.

Implements the six-point checklist from STRATEGY_SCHEMA.md Section 11.
Handles approve, reject, list-pending, and review summary generation.

No LLM calls. All logic is deterministic Python.
Session lifecycle is managed by the caller.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import Session

from quantforge.database.models.strategy import StrategyRecord
from quantforge.database.repositories.strategy import (
    InvalidStatusTransitionError,
    StrategyRepository,
)
from quantforge.strategy.models.enums import (
    ProvenanceLabel,
    StrategyStatus,
)
from quantforge.strategy.models.spec import (
    PillarData,
    StrategySpecification,
)

logger = logging.getLogger(__name__)

ALL_PILLARS = [
    "market_regime", "market_eligibility", "asset_selection",
    "entry_signal", "position_sizing", "risk_management",
    "exit", "portfolio", "backtesting",
]


# ---------------------------------------------------------------------------
# Checklist result
# ---------------------------------------------------------------------------


@dataclass
class ChecklistItem:
    number: int
    description: str
    passed: bool
    detail: str = ""


@dataclass
class ReviewChecklist:
    """Result of running the six-point review checklist."""
    items: list[ChecklistItem] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(item.passed for item in self.items)

    @property
    def failures(self) -> list[ChecklistItem]:
        return [item for item in self.items if not item.passed]


# ---------------------------------------------------------------------------
# Review summary
# ---------------------------------------------------------------------------


@dataclass
class ReviewSummary:
    """What the reviewer sees for one spec."""
    record: StrategyRecord
    spec: StrategySpecification | None
    checklist: ReviewChecklist
    load_error: str | None = None   # set when spec JSON could not be loaded


# ---------------------------------------------------------------------------
# Six-point checklist
# ---------------------------------------------------------------------------


def run_checklist(spec: StrategySpecification) -> ReviewChecklist:
    """
    Run the six-point review checklist from STRATEGY_SCHEMA.md Section 11.

    1. Every rule has a valid provenance label.
    2. Every 'inferred' rule has evidence recorded.
    3. No 'quantforge_added' rules appear in the base spec.
    4. Missing pillars are recorded, not silently skipped.
    5. The completeness classification matches the actual coverage.
    6. The methodology assessment is consistent with the extracted content.
    """
    checklist = ReviewChecklist()
    pillar_map: dict[str, PillarData] = {
        "market_regime": spec.pillars.market_regime,
        "market_eligibility": spec.pillars.market_eligibility,
        "asset_selection": spec.pillars.asset_selection,
        "entry_signal": spec.pillars.entry_signal,
        "position_sizing": spec.pillars.position_sizing,
        "risk_management": spec.pillars.risk_management,
        "exit": spec.pillars.exit,
        "portfolio": spec.pillars.portfolio,
        "backtesting": spec.pillars.backtesting,
    }

    # --- Check 1: every rule has a provenance label ---
    missing_provenance: list[str] = []
    for pillar_name, pillar_data in pillar_map.items():
        for rule in pillar_data.rules:
            if not rule.provenance:
                missing_provenance.append(f"{pillar_name}/{rule.rule_id}")

    checklist.items.append(ChecklistItem(
        number=1,
        description="Every rule has a valid provenance label.",
        passed=not missing_provenance,
        detail=(
            f"Rules missing provenance: {missing_provenance}"
            if missing_provenance else ""
        ),
    ))

    # --- Check 2: every inferred rule has evidence ---
    missing_evidence: list[str] = []
    for pillar_name, pillar_data in pillar_map.items():
        for rule in pillar_data.rules:
            if rule.provenance == ProvenanceLabel.INFERRED and not rule.evidence:
                missing_evidence.append(f"{pillar_name}/{rule.rule_id}")

    checklist.items.append(ChecklistItem(
        number=2,
        description="Every 'inferred' rule has evidence recorded.",
        passed=not missing_evidence,
        detail=(
            f"Inferred rules missing evidence: {missing_evidence}"
            if missing_evidence else ""
        ),
    ))

    # --- Check 3: no quantforge_added rules in base spec ---
    qf_rules: list[str] = []
    if spec.variant == "base":
        for pillar_name, pillar_data in pillar_map.items():
            for rule in pillar_data.rules:
                if rule.provenance == ProvenanceLabel.QUANTFORGE_ADDED:
                    qf_rules.append(f"{pillar_name}/{rule.rule_id}")

    checklist.items.append(ChecklistItem(
        number=3,
        description="No 'quantforge_added' rules appear in the base spec.",
        passed=not qf_rules,
        detail=(
            f"'quantforge_added' rules in base spec: {qf_rules}"
            if qf_rules else ""
        ),
    ))

    # --- Check 4: absent pillars are recorded in missing_components ---
    absent_pillars = {p for p, d in pillar_map.items() if d.present is False}
    recorded_in_missing = {mc.pillar for mc in spec.missing_components}
    unrecorded = absent_pillars - recorded_in_missing

    checklist.items.append(ChecklistItem(
        number=4,
        description="Missing pillars are recorded in missing_components, not silently skipped.",
        passed=not unrecorded,
        detail=(
            f"Absent pillars not recorded: {sorted(unrecorded)}"
            if unrecorded else ""
        ),
    ))

    # --- Check 5: completeness classification matches actual coverage ---
    # Heuristic: if entry_signal is present, class must include entry-related terms.
    # We only flag the most obvious contradictions — full semantic check is human judgment.
    cc = spec.completeness_class.value
    entry_present = pillar_map["entry_signal"].present is True
    exit_present = pillar_map["exit"].present is True
    sizing_present = pillar_map["position_sizing"].present is True
    risk_present = pillar_map["risk_management"].present is True

    all_core_present = entry_present and exit_present and sizing_present and risk_present
    contradiction = False
    contradiction_detail = ""
    if cc == "complete_strategy" and not all_core_present:
        missing_core = [
            p for p, present in [
                ("entry_signal", entry_present),
                ("exit", exit_present),
                ("position_sizing", sizing_present),
                ("risk_management", risk_present),
            ] if not present
        ]
        contradiction = True
        contradiction_detail = (
            f"Class is 'complete_strategy' but these core pillars are absent: {missing_core}"
        )
    elif cc == "research_only":
        any_rules = any(len(d.rules) > 0 for d in pillar_map.values())
        if any_rules:
            contradiction = True
            contradiction_detail = (
                "Class is 'research_only' but rules were extracted — "
                "consider a more specific completeness class."
            )

    checklist.items.append(ChecklistItem(
        number=5,
        description="The completeness classification matches the actual coverage.",
        passed=not contradiction,
        detail=contradiction_detail,
    ))

    # --- Check 6: methodology assessment is present and internally consistent ---
    ma_ok = True
    ma_detail = ""
    if spec.methodology_assessment is None:
        ma_ok = False
        ma_detail = "methodology_assessment is absent."
    else:
        ma = spec.methodology_assessment
        if not ma.primary_strength or not ma.primary_weakness or not ma.reason:
            ma_ok = False
            ma_detail = (
                "methodology_assessment is incomplete — "
                "primary_strength, primary_weakness, and reason are all required."
            )

    checklist.items.append(ChecklistItem(
        number=6,
        description="The methodology assessment is present and internally consistent.",
        passed=ma_ok,
        detail=ma_detail,
    ))

    return checklist


# ---------------------------------------------------------------------------
# Review service
# ---------------------------------------------------------------------------


class ReviewService:
    """
    All review workflow operations.
    Session lifecycle is managed by the caller.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self._repo = StrategyRepository(session)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def list_pending_review(self) -> list[StrategyRecord]:
        """Return all specs with status under_review."""
        return self._repo.list_by_status(StrategyStatus.UNDER_REVIEW)

    def get_review_summary(self, strategy_id: UUID) -> ReviewSummary:
        """
        Load a spec record + its JSON file, run the checklist,
        and return a ReviewSummary for display.
        """
        record = self._repo.get_by_id(strategy_id)
        if record is None:
            raise ValueError(f"Strategy spec {strategy_id} not found.")

        spec: StrategySpecification | None = None
        load_error: str | None = None

        if record.file_path and Path(record.file_path).exists():
            try:
                data = json.loads(Path(record.file_path).read_text(encoding="utf-8"))
                spec = StrategySpecification(**data)
            except Exception as e:
                load_error = f"Could not load spec JSON: {e}"
                logger.error("ReviewService: %s", load_error)
        elif record.file_path:
            load_error = f"Spec file not found on disk: {record.file_path}"
        else:
            load_error = "No spec file path recorded (draft failure record)."

        if spec is not None:
            checklist = run_checklist(spec)
        else:
            # Cannot run checklist without the spec
            checklist = ReviewChecklist(items=[
                ChecklistItem(
                    number=i,
                    description="N/A",
                    passed=False,
                    detail="Spec could not be loaded.",
                )
                for i in range(1, 7)
            ])

        return ReviewSummary(
            record=record,
            spec=spec,
            checklist=checklist,
            load_error=load_error,
        )

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def approve_spec(
        self,
        strategy_id: UUID,
        reviewer_id: str,
    ) -> StrategyRecord:
        """
        Approve a spec after the checklist passes.

        Raises:
            ValueError: spec not found or checklist fails.
            InvalidStatusTransitionError: status is not under_review.
        """
        summary = self.get_review_summary(strategy_id)
        if not summary.checklist.passed:
            failures = "; ".join(
                f"[{f.number}] {f.description}: {f.detail}"
                for f in summary.checklist.failures
            )
            raise ValueError(
                f"Cannot approve spec {strategy_id} — checklist failures:\n{failures}"
            )

        record = self._repo.update_status(
            strategy_id=strategy_id,
            new_status=StrategyStatus.APPROVED,
            reviewer_id=reviewer_id,
        )
        logger.info("ReviewService: approved spec %s by %r", strategy_id, reviewer_id)
        return record

    def reject_spec(
        self,
        strategy_id: UUID,
        reviewer_id: str,
        notes: str,
    ) -> StrategyRecord:
        """
        Reject a spec with mandatory reviewer notes.

        Raises:
            ValueError: notes are empty, or spec not found.
            InvalidStatusTransitionError: status transition not allowed.
        """
        if not notes or not notes.strip():
            raise ValueError(
                "Rejection requires non-empty notes explaining what needs to be corrected."
            )

        record = self._repo.update_status(
            strategy_id=strategy_id,
            new_status=StrategyStatus.REJECTED,
            reviewer_id=reviewer_id,
        )
        logger.info(
            "ReviewService: rejected spec %s by %r — %s",
            strategy_id, reviewer_id, notes,
        )
        return record
