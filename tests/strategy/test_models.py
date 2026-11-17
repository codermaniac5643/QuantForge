# tests/strategy/test_models.py
"""
Unit tests for Strategy Specification Pydantic models — Milestone 12.
No database, no I/O.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from quantforge.strategy.models import (
    ALLOWED_STATUS_TRANSITIONS,
    CompletenessClass,
    ComponentCoverage,
    ComponentCoverageMap,
    ExtractionAudit,
    ExtractionMethod,
    Implementability,
    MethodologyAssessment,
    MissingAction,
    MissingComponent,
    MissingImpact,
    PillarData,
    ProvenanceLabel,
    RuleObject,
    StrategyPillars,
    StrategySpecification,
    StrategyStatus,
    StrategyVariant,
    is_valid_status_transition,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_rule(
    rule_id: str = "R001",
    provenance: ProvenanceLabel = ProvenanceLabel.EXPLICIT,
    evidence: str | None = None,
) -> RuleObject:
    return RuleObject(
        rule_id=rule_id,
        description="12-month momentum lookback",
        provenance=provenance,
        source_ref="Section 3.1",
        evidence=evidence,
    )


def make_spec(**overrides) -> StrategySpecification:
    defaults = dict(
        paper_id=uuid.uuid4(),
        extracted_by=ExtractionMethod.LLM_ASSISTED,
        completeness_class=CompletenessClass.ENTRY_FOCUSED,
    )
    defaults.update(overrides)
    return StrategySpecification(**defaults)


# ---------------------------------------------------------------------------
# RuleObject
# ---------------------------------------------------------------------------


class TestRuleObject:
    def test_explicit_rule_no_evidence_ok(self):
        rule = make_rule(provenance=ProvenanceLabel.EXPLICIT)
        assert rule.rule_id == "R001"
        assert rule.evidence is None

    def test_inferred_rule_with_evidence_ok(self):
        rule = make_rule(
            provenance=ProvenanceLabel.INFERRED,
            evidence="Authors imply a 1% stop-loss in Table 2.",
        )
        assert rule.provenance == ProvenanceLabel.INFERRED

    def test_inferred_rule_missing_evidence_raises(self):
        with pytest.raises(ValidationError, match="evidence"):
            make_rule(provenance=ProvenanceLabel.INFERRED, evidence=None)

    def test_quantforge_added_no_evidence_ok(self):
        # quantforge_added does not require evidence
        rule = make_rule(provenance=ProvenanceLabel.QUANTFORGE_ADDED)
        assert rule.provenance == ProvenanceLabel.QUANTFORGE_ADDED

    def test_all_optional_fields_roundtrip(self):
        rule = RuleObject(
            rule_id="R002",
            description="ATR-based position size",
            provenance=ProvenanceLabel.EXPLICIT,
            source_ref="Eq. (5)",
            formula="size = capital * risk_pct / (ATR * multiplier)",
            timeframe="daily",
            data_inputs="OHLCV, 14-day ATR",
            notes="Standard volatility sizing",
        )
        data = rule.model_dump()
        restored = RuleObject(**data)
        assert restored.formula == rule.formula


# ---------------------------------------------------------------------------
# PillarData
# ---------------------------------------------------------------------------


class TestPillarData:
    def test_default_pillar_absent(self):
        p = PillarData()
        assert p.present is False
        assert p.rules == []

    def test_partial_present(self):
        p = PillarData(present="partial")
        assert p.present == "partial"

    def test_invalid_present_raises(self):
        with pytest.raises(ValidationError):
            PillarData(present="maybe")

    def test_pillar_with_rules(self):
        p = PillarData(present=True, rules=[make_rule()])
        assert len(p.rules) == 1


# ---------------------------------------------------------------------------
# MissingComponent
# ---------------------------------------------------------------------------


class TestMissingComponent:
    def test_valid_missing_component(self):
        mc = MissingComponent(
            pillar="exit",
            status="absent",
            impact=MissingImpact.CRITICAL,
            description="No exit rules defined",
            implication="Cannot close positions",
            recommended_action=MissingAction.BLOCKS_IMPLEMENTATION,
        )
        assert mc.status == "absent"

    def test_invalid_status_raises(self):
        with pytest.raises(ValidationError):
            MissingComponent(
                pillar="exit",
                status="unknown",
                impact=MissingImpact.MINOR,
                description="d",
                implication="i",
                recommended_action=MissingAction.IGNORE,
            )


# ---------------------------------------------------------------------------
# StrategySpecification base spec
# ---------------------------------------------------------------------------


class TestStrategySpecification:
    def test_minimal_spec_ok(self):
        spec = make_spec()
        assert spec.variant == "base"
        assert spec.status == StrategyStatus.DRAFT

    def test_quantforge_added_rule_in_base_raises(self):
        qf_rule = make_rule(rule_id="QF001", provenance=ProvenanceLabel.QUANTFORGE_ADDED)
        pillars = StrategyPillars(
            entry_signal=PillarData(present=True, rules=[qf_rule])
        )
        with pytest.raises(ValidationError, match="quantforge_added"):
            StrategySpecification(
                paper_id=uuid.uuid4(),
                extracted_by=ExtractionMethod.MANUAL,
                completeness_class=CompletenessClass.ENTRY_FOCUSED,
                pillars=pillars,
            )

    def test_quantforge_added_allowed_in_non_base_variant(self):
        qf_rule = make_rule(rule_id="QF001", provenance=ProvenanceLabel.QUANTFORGE_ADDED)
        pillars = StrategyPillars(
            entry_signal=PillarData(present=True, rules=[qf_rule])
        )
        # variant != "base" should NOT raise
        spec = StrategySpecification(
            paper_id=uuid.uuid4(),
            extracted_by=ExtractionMethod.MANUAL,
            completeness_class=CompletenessClass.ENTRY_FOCUSED,
            variant="enhanced",
            pillars=pillars,
        )
        assert spec.variant == "enhanced"

    def test_serialise_deserialise_roundtrip(self):
        spec = make_spec(
            pillars=StrategyPillars(
                entry_signal=PillarData(
                    present=True,
                    rules=[make_rule()],
                )
            )
        )
        data = spec.model_dump()
        restored = StrategySpecification(**data)
        assert restored.strategy_id == spec.strategy_id
        assert len(restored.pillars.entry_signal.rules) == 1


# ---------------------------------------------------------------------------
# ExtractionAudit
# ---------------------------------------------------------------------------


class TestExtractionAudit:
    def test_defaults_ok(self):
        audit = ExtractionAudit()
        assert audit.total_input_tokens == 0
        assert audit.routing_map == {}

    def test_roundtrip(self):
        audit = ExtractionAudit(
            routing_map={"entry_signal": [2, 3]},
            sections_used=[2, 3],
            sections_skipped=[0],
            sections_filtered_too_short=[1],
            total_input_tokens=1500,
            total_output_tokens=300,
        )
        data = audit.model_dump()
        restored = ExtractionAudit(**data)
        assert restored.routing_map == {"entry_signal": [2, 3]}


# ---------------------------------------------------------------------------
# StrategyVariant
# ---------------------------------------------------------------------------


class TestStrategyVariant:
    def test_valid_variant(self):
        variant = StrategyVariant(
            variant_id="enhanced-v1",
            based_on_strategy_id=uuid.uuid4(),
            based_on_version=1,
            description="Adds ATR stop-loss",
            rationale="Paper lacks explicit exit rules",
            added_rules=[make_rule(provenance=ProvenanceLabel.QUANTFORGE_ADDED)],
        )
        assert variant.variant_id == "enhanced-v1"
        assert len(variant.added_rules) == 1


# ---------------------------------------------------------------------------
# Enum roundtrips
# ---------------------------------------------------------------------------


class TestEnumRoundtrips:
    @pytest.mark.parametrize("status", list(StrategyStatus))
    def test_strategy_status_str_roundtrip(self, status: StrategyStatus):
        assert StrategyStatus(status.value) == status

    @pytest.mark.parametrize("cls", list(CompletenessClass))
    def test_completeness_class_str_roundtrip(self, cls: CompletenessClass):
        assert CompletenessClass(cls.value) == cls

    @pytest.mark.parametrize("prov", list(ProvenanceLabel))
    def test_provenance_roundtrip(self, prov: ProvenanceLabel):
        assert ProvenanceLabel(prov.value) == prov

    @pytest.mark.parametrize("cov", list(ComponentCoverage))
    def test_component_coverage_roundtrip(self, cov: ComponentCoverage):
        assert ComponentCoverage(cov.value) == cov


# ---------------------------------------------------------------------------
# Status transition guard
# ---------------------------------------------------------------------------


class TestStatusTransitions:
    @pytest.mark.parametrize(
        "frm, to, expected",
        [
            (StrategyStatus.DRAFT, StrategyStatus.UNDER_REVIEW, True),
            (StrategyStatus.DRAFT, StrategyStatus.REJECTED, True),
            (StrategyStatus.DRAFT, StrategyStatus.APPROVED, False),  # skip step
            (StrategyStatus.UNDER_REVIEW, StrategyStatus.APPROVED, True),
            (StrategyStatus.UNDER_REVIEW, StrategyStatus.REJECTED, True),
            (StrategyStatus.APPROVED, StrategyStatus.SUPERSEDED, True),
            (StrategyStatus.APPROVED, StrategyStatus.DRAFT, False),
            (StrategyStatus.REJECTED, StrategyStatus.DRAFT, True),  # re-extract
            (StrategyStatus.SUPERSEDED, StrategyStatus.DRAFT, False),  # terminal
        ],
    )
    def test_transition(
        self,
        frm: StrategyStatus,
        to: StrategyStatus,
        expected: bool,
    ):
        assert is_valid_status_transition(frm, to) == expected

    def test_allowed_transitions_dict_complete(self):
        """Every StrategyStatus must appear in ALLOWED_STATUS_TRANSITIONS."""
        for status in StrategyStatus:
            assert status in ALLOWED_STATUS_TRANSITIONS
