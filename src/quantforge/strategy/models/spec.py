# src/quantforge/strategy/models/spec.py
"""
Strategy Specification Pydantic models — Milestone 12.

These models define the in-memory structure of an extracted strategy spec.
They are serialised to JSON and stored on disk; the DB holds only metadata.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

from quantforge.strategy.models.enums import (
    CompletenessClass,
    ComponentCoverage,
    ExtractionMethod,
    Implementability,
    MissingAction,
    MissingImpact,
    ProvenanceLabel,
    StrategyStatus,
)


# ---------------------------------------------------------------------------
# Rule-level model
# ---------------------------------------------------------------------------


class RuleObject(BaseModel):
    """A single trading rule extracted from a research paper."""

    rule_id: str
    description: str
    provenance: ProvenanceLabel
    source_ref: str  # e.g. "Section 3.2, Equation 4"

    formula: str | None = None
    timeframe: str | None = None
    data_inputs: str | None = None
    evidence: str | None = None   # required when provenance == inferred
    notes: str | None = None

    @model_validator(mode="after")
    def evidence_required_for_inferred(self) -> RuleObject:
        if (
            self.provenance == ProvenanceLabel.INFERRED
            and not self.evidence
        ):
            raise ValueError(
                f"Rule {self.rule_id!r}: 'evidence' is required when "
                "provenance is 'inferred'."
            )
        return self


# ---------------------------------------------------------------------------
# Pillar model
# ---------------------------------------------------------------------------


class PillarData(BaseModel):
    """Data for a single strategy pillar."""

    present: bool | str = False   # True, False, or "partial"
    source_note: str | None = None
    rules: list[RuleObject] = Field(default_factory=list)
    missing_note: str | None = None

    @model_validator(mode="after")
    def validate_present_value(self) -> PillarData:
        if not isinstance(self.present, bool) and self.present != "partial":
            raise ValueError(
                "'present' must be True, False, or the string 'partial'."
            )
        return self


# ---------------------------------------------------------------------------
# Missing component
# ---------------------------------------------------------------------------


class MissingComponent(BaseModel):
    """A pillar that is absent or only partially covered."""

    pillar: str
    status: str    # "absent" | "partial"
    impact: MissingImpact
    description: str
    implication: str
    recommended_action: MissingAction

    @model_validator(mode="after")
    def validate_status(self) -> MissingComponent:
        if self.status not in {"absent", "partial"}:
            raise ValueError(
                f"MissingComponent.status must be 'absent' or 'partial', "
                f"got {self.status!r}"
            )
        return self


# ---------------------------------------------------------------------------
# Methodology assessment
# ---------------------------------------------------------------------------


class MethodologyAssessment(BaseModel):
    """High-level assessment of a strategy's methodology."""

    implementability: Implementability
    suitable_for_backtest: bool
    primary_strength: str
    primary_weakness: str
    key_missing_components: list[str] = Field(default_factory=list)
    reason: str


# ---------------------------------------------------------------------------
# Component coverage map
# ---------------------------------------------------------------------------


class ComponentCoverageMap(BaseModel):
    """Coverage level for each of the nine strategy pillars."""

    market_regime: ComponentCoverage = ComponentCoverage.ABSENT
    market_eligibility: ComponentCoverage = ComponentCoverage.ABSENT
    asset_selection: ComponentCoverage = ComponentCoverage.ABSENT
    entry_signal: ComponentCoverage = ComponentCoverage.ABSENT
    position_sizing: ComponentCoverage = ComponentCoverage.ABSENT
    risk_management: ComponentCoverage = ComponentCoverage.ABSENT
    exit: ComponentCoverage = ComponentCoverage.ABSENT
    portfolio: ComponentCoverage = ComponentCoverage.ABSENT
    backtesting: ComponentCoverage = ComponentCoverage.ABSENT


# ---------------------------------------------------------------------------
# Nine-pillar container
# ---------------------------------------------------------------------------


class StrategyPillars(BaseModel):
    """All nine strategy pillars."""

    market_regime: PillarData = Field(default_factory=PillarData)
    market_eligibility: PillarData = Field(default_factory=PillarData)
    asset_selection: PillarData = Field(default_factory=PillarData)
    entry_signal: PillarData = Field(default_factory=PillarData)
    position_sizing: PillarData = Field(default_factory=PillarData)
    risk_management: PillarData = Field(default_factory=PillarData)
    exit: PillarData = Field(default_factory=PillarData)
    portfolio: PillarData = Field(default_factory=PillarData)
    backtesting: PillarData = Field(default_factory=PillarData)


# ---------------------------------------------------------------------------
# Extraction audit (Section 9.1 of STRATEGY_SCHEMA.md)
# ---------------------------------------------------------------------------


class ExtractionAudit(BaseModel):
    """Traceability data written by the extraction pipeline."""

    routing_map: dict[str, list[int]] = Field(default_factory=dict)
    sections_used: list[int] = Field(default_factory=list)
    sections_skipped: list[int] = Field(default_factory=list)
    sections_filtered_too_short: list[int] = Field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    extraction_date: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ---------------------------------------------------------------------------
# Top-level spec
# ---------------------------------------------------------------------------


class StrategySpecification(BaseModel):
    """
    The full extracted strategy specification for one paper.

    Rules with provenance='quantforge_added' are forbidden in a base spec
    (they may appear only in StrategyVariant.added_rules).
    """

    strategy_id: UUID = Field(default_factory=uuid4)
    paper_id: UUID
    version: int = 1
    variant: str = "base"

    status: StrategyStatus = StrategyStatus.DRAFT
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    extracted_by: ExtractionMethod
    reviewed_by: str | None = None

    completeness_class: CompletenessClass
    component_coverage: ComponentCoverageMap = Field(
        default_factory=ComponentCoverageMap
    )

    notes: str | None = None
    pillars: StrategyPillars = Field(default_factory=StrategyPillars)
    missing_components: list[MissingComponent] = Field(default_factory=list)
    methodology_assessment: MethodologyAssessment | None = None

    extraction_metadata: dict[str, Any] = Field(default_factory=dict)
    extraction_audit: ExtractionAudit | None = None

    @model_validator(mode="after")
    def no_quantforge_added_rules_in_base(self) -> StrategySpecification:
        """
        Base specs must not contain quantforge_added rules.
        Those belong exclusively in StrategyVariant.added_rules.
        """
        if self.variant != "base":
            return self

        pillar_map: dict[str, PillarData] = {
            "market_regime": self.pillars.market_regime,
            "market_eligibility": self.pillars.market_eligibility,
            "asset_selection": self.pillars.asset_selection,
            "entry_signal": self.pillars.entry_signal,
            "position_sizing": self.pillars.position_sizing,
            "risk_management": self.pillars.risk_management,
            "exit": self.pillars.exit,
            "portfolio": self.pillars.portfolio,
            "backtesting": self.pillars.backtesting,
        }
        bad: list[str] = []
        for pillar_name, pillar_data in pillar_map.items():
            for rule in pillar_data.rules:
                if rule.provenance == ProvenanceLabel.QUANTFORGE_ADDED:
                    bad.append(
                        f"{pillar_name}/{rule.rule_id}"
                    )
        if bad:
            raise ValueError(
                "Base specs must not contain 'quantforge_added' rules. "
                f"Found in: {bad}"
            )
        return self


# ---------------------------------------------------------------------------
# Variant model
# ---------------------------------------------------------------------------


class StrategyVariant(BaseModel):
    """
    A named variant of a base strategy spec.
    Only variants may carry quantforge_added rules.
    """

    variant_id: str
    based_on_strategy_id: UUID
    based_on_version: int
    description: str
    added_rules: list[RuleObject] = Field(default_factory=list)
    modified_rules: list[dict[str, Any]] = Field(default_factory=list)
    rationale: str
