# tests/strategy/test_variant_service.py
"""
Tests for VariantService — Milestone 16.

Coverage:
- create_variant: happy path, immutability of base, bad provenance rules,
  non-approved base, reserved name, version incrementing
- list_variants: empty, populated, does not include base
- get_variant_spec: happy path, missing file, base record raises
- DB record created with correct fields
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from quantforge.database.base import Base
from quantforge.database.models.paper import PaperRecord  # noqa: F401 — FK
from quantforge.database.models.strategy import StrategyRecord
from quantforge.database.repositories.strategy import StrategyRepository
from quantforge.models.states import PaperState
from quantforge.strategy.models.enums import (
    CompletenessClass,
    ExtractionMethod,
    Implementability,
    MissingAction,
    MissingImpact,
    ProvenanceLabel,
    StrategyStatus,
)
from quantforge.strategy.models.spec import (
    ComponentCoverageMap,
    MethodologyAssessment,
    MissingComponent,
    PillarData,
    RuleObject,
    StrategyPillars,
    StrategySpecification,
    StrategyVariant,
)
from quantforge.strategy.variant_service import (
    VariantError,
    VariantService,
    VariantWriteResult,
)

TEST_DB_URL = "postgresql://postgres:postgres@localhost:5432/quantforge_test"


# ---------------------------------------------------------------------------
# DB fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def var_engine():
    engine = create_engine(TEST_DB_URL)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(scope="function")
def var_session(var_engine):
    connection = var_engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection)
    session = Session()
    yield session
    session.close()
    transaction.rollback()
    connection.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_paper(session) -> PaperRecord:
    paper = PaperRecord(
        id=uuid.uuid4(),
        title="Momentum Strategy",
        arxiv_id=f"test.{uuid.uuid4().hex[:8]}",
        abstract="Abstract.",
        authors=[], keywords=[], sources=[],
        state=PaperState.PROCESSED.value,
    )
    session.add(paper)
    session.flush()
    return paper


def _make_rule(
    rule_id: str = "R001",
    provenance: ProvenanceLabel = ProvenanceLabel.EXPLICIT,
) -> RuleObject:
    return RuleObject(
        rule_id=rule_id,
        description="12-month momentum lookback",
        provenance=provenance,
        source_ref="Section 3.1",
    )


def _make_qf_rule(rule_id: str = "QF001") -> RuleObject:
    return RuleObject(
        rule_id=rule_id,
        description="Custom ATR stop-loss added by QuantForge",
        provenance=ProvenanceLabel.QUANTFORGE_ADDED,
        source_ref="QuantForge rule library",
    )


def _make_spec(paper_id: uuid.UUID) -> StrategySpecification:
    return StrategySpecification(
        paper_id=paper_id,
        extracted_by=ExtractionMethod.LLM_ASSISTED,
        completeness_class=CompletenessClass.ENTRY_FOCUSED,
        component_coverage=ComponentCoverageMap(),
        pillars=StrategyPillars(
            entry_signal=PillarData(present=True, rules=[_make_rule()])
        ),
        missing_components=[
            MissingComponent(
                pillar=p, status="absent",
                impact=MissingImpact.MODERATE,
                description=f"{p} not found.",
                implication="Cannot implement.",
                recommended_action=MissingAction.REQUIRES_HUMAN_DECISION,
            )
            for p in [
                "market_regime", "market_eligibility", "asset_selection",
                "position_sizing", "risk_management", "exit", "portfolio", "backtesting",
            ]
        ],
        methodology_assessment=MethodologyAssessment(
            implementability=Implementability.REQUIRES_ADDITIONS,
            suitable_for_backtest=True,
            primary_strength="Clear entry rules.",
            primary_weakness="No exit.",
            reason="Entry OK, exit absent.",
        ),
    )


def _persist_base_record(
    session, paper_id: uuid.UUID, spec: StrategySpecification,
    tmp_path: Path, status: StrategyStatus = StrategyStatus.APPROVED,
) -> StrategyRecord:
    """Write spec JSON and create an APPROVED StrategyRecord."""
    spec_path = tmp_path / str(paper_id) / "strategy" / "spec_v1.json"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(
        json.dumps(spec.model_dump(mode="json"), indent=2), encoding="utf-8"
    )
    record = StrategyRecord(
        id=spec.strategy_id,
        paper_id=paper_id,
        version=1,
        variant="base",
        status=status.value,
        completeness_class=spec.completeness_class.value,
        file_path=str(spec_path),
        extracted_by=ExtractionMethod.LLM_ASSISTED.value,
    )
    session.add(record)
    session.flush()
    return record


# ---------------------------------------------------------------------------
# create_variant
# ---------------------------------------------------------------------------


class TestCreateVariant:
    def test_creates_file_and_db_record(self, var_session, tmp_path):
        paper = _make_paper(var_session)
        spec = _make_spec(paper.id)
        _persist_base_record(var_session, paper.id, spec, tmp_path)

        svc = VariantService(var_session, tmp_path)
        result = svc.create_variant(
            base_strategy_id=spec.strategy_id,
            variant_name="with_exit",
            description="Adds ATR stop-loss exit.",
            rationale="Base has no exit rules.",
            added_rules=[_make_qf_rule()],
        )

        assert isinstance(result, VariantWriteResult)
        assert result.file_path.exists()
        assert result.variant_name == "with_exit"
        assert result.version == 1

        # DB record
        repo = StrategyRepository(var_session)
        record = repo.get_by_id(result.strategy_id)
        assert record is not None
        assert record.variant == "with_exit"
        assert record.status == StrategyStatus.DRAFT.value

    def test_base_spec_not_modified(self, var_session, tmp_path):
        """Creating a variant must not change the base spec JSON file."""
        paper = _make_paper(var_session)
        spec = _make_spec(paper.id)
        base_record = _persist_base_record(var_session, paper.id, spec, tmp_path)
        base_content_before = Path(base_record.file_path).read_text()

        svc = VariantService(var_session, tmp_path)
        svc.create_variant(
            base_strategy_id=spec.strategy_id,
            variant_name="enhanced",
            description="Adds rules.",
            rationale="Extend base.",
            added_rules=[_make_qf_rule()],
        )

        base_content_after = Path(base_record.file_path).read_text()
        assert base_content_before == base_content_after

    def test_variant_file_in_correct_location(self, var_session, tmp_path):
        paper = _make_paper(var_session)
        spec = _make_spec(paper.id)
        _persist_base_record(var_session, paper.id, spec, tmp_path)

        svc = VariantService(var_session, tmp_path)
        result = svc.create_variant(
            base_strategy_id=spec.strategy_id,
            variant_name="my_variant",
            description="d", rationale="r", added_rules=[],
        )

        expected = tmp_path / str(paper.id) / "strategy" / "variants" / "my_variant_v1.json"
        assert result.file_path == expected

    def test_version_increments_on_second_variant_of_same_name(self, var_session, tmp_path):
        paper = _make_paper(var_session)
        spec = _make_spec(paper.id)
        _persist_base_record(var_session, paper.id, spec, tmp_path)

        svc = VariantService(var_session, tmp_path)
        r1 = svc.create_variant(
            base_strategy_id=spec.strategy_id,
            variant_name="enhanced", description="d", rationale="r", added_rules=[],
        )
        r2 = svc.create_variant(
            base_strategy_id=spec.strategy_id,
            variant_name="enhanced", description="d2", rationale="r2", added_rules=[],
        )
        assert r1.version == 1
        assert r2.version == 2

    def test_non_approved_base_raises(self, var_session, tmp_path):
        paper = _make_paper(var_session)
        spec = _make_spec(paper.id)
        _persist_base_record(var_session, paper.id, spec, tmp_path, StrategyStatus.UNDER_REVIEW)

        svc = VariantService(var_session, tmp_path)
        with pytest.raises(VariantError, match="APPROVED"):
            svc.create_variant(
                base_strategy_id=spec.strategy_id,
                variant_name="v1", description="d", rationale="r", added_rules=[],
            )

    def test_reserved_name_base_raises(self, var_session, tmp_path):
        paper = _make_paper(var_session)
        spec = _make_spec(paper.id)
        _persist_base_record(var_session, paper.id, spec, tmp_path)

        svc = VariantService(var_session, tmp_path)
        with pytest.raises(VariantError, match="reserved"):
            svc.create_variant(
                base_strategy_id=spec.strategy_id,
                variant_name="base", description="d", rationale="r", added_rules=[],
            )

    def test_non_quantforge_added_rule_raises(self, var_session, tmp_path):
        paper = _make_paper(var_session)
        spec = _make_spec(paper.id)
        _persist_base_record(var_session, paper.id, spec, tmp_path)

        bad_rule = _make_rule(rule_id="R999", provenance=ProvenanceLabel.EXPLICIT)
        svc = VariantService(var_session, tmp_path)
        with pytest.raises(VariantError, match="quantforge_added"):
            svc.create_variant(
                base_strategy_id=spec.strategy_id,
                variant_name="bad", description="d", rationale="r",
                added_rules=[bad_rule],
            )

    def test_base_record_not_found_raises(self, var_session, tmp_path):
        svc = VariantService(var_session, tmp_path)
        with pytest.raises(VariantError, match="not found"):
            svc.create_variant(
                base_strategy_id=uuid.uuid4(),
                variant_name="v", description="d", rationale="r", added_rules=[],
            )

    def test_empty_variant_name_raises(self, var_session, tmp_path):
        paper = _make_paper(var_session)
        spec = _make_spec(paper.id)
        _persist_base_record(var_session, paper.id, spec, tmp_path)

        svc = VariantService(var_session, tmp_path)
        with pytest.raises(VariantError, match="non-empty"):
            svc.create_variant(
                base_strategy_id=spec.strategy_id,
                variant_name="", description="d", rationale="r", added_rules=[],
            )

    def test_variant_json_contains_correct_fields(self, var_session, tmp_path):
        paper = _make_paper(var_session)
        spec = _make_spec(paper.id)
        _persist_base_record(var_session, paper.id, spec, tmp_path)

        svc = VariantService(var_session, tmp_path)
        result = svc.create_variant(
            base_strategy_id=spec.strategy_id,
            variant_name="check_fields",
            description="Desc",
            rationale="Rationale",
            added_rules=[_make_qf_rule("QF001")],
        )
        data = json.loads(result.file_path.read_text())
        assert data["based_on_strategy_id"] == str(spec.strategy_id)
        assert data["based_on_version"] == 1
        assert len(data["added_rules"]) == 1
        assert data["added_rules"][0]["rule_id"] == "QF001"
        assert data["added_rules"][0]["provenance"] == "quantforge_added"


# ---------------------------------------------------------------------------
# list_variants
# ---------------------------------------------------------------------------


class TestListVariants:
    def test_empty_when_no_variants(self, var_session, tmp_path):
        paper = _make_paper(var_session)
        svc = VariantService(var_session, tmp_path)
        assert svc.list_variants(paper.id) == []

    def test_does_not_return_base_spec(self, var_session, tmp_path):
        paper = _make_paper(var_session)
        spec = _make_spec(paper.id)
        _persist_base_record(var_session, paper.id, spec, tmp_path)

        svc = VariantService(var_session, tmp_path)
        # Only the base record exists — list_variants should return empty
        variants = svc.list_variants(paper.id)
        assert all(r.variant != "base" for r in variants)
        assert len(variants) == 0

    def test_returns_variant_records(self, var_session, tmp_path):
        paper = _make_paper(var_session)
        spec = _make_spec(paper.id)
        _persist_base_record(var_session, paper.id, spec, tmp_path)

        svc = VariantService(var_session, tmp_path)
        svc.create_variant(
            base_strategy_id=spec.strategy_id,
            variant_name="v1", description="d", rationale="r", added_rules=[],
        )
        svc.create_variant(
            base_strategy_id=spec.strategy_id,
            variant_name="v2", description="d", rationale="r", added_rules=[],
        )
        variants = svc.list_variants(paper.id)
        assert len(variants) == 2
        variant_names = {r.variant for r in variants}
        assert "v1" in variant_names
        assert "v2" in variant_names


# ---------------------------------------------------------------------------
# get_variant_spec
# ---------------------------------------------------------------------------


class TestGetVariantSpec:
    def test_loads_variant_correctly(self, var_session, tmp_path):
        paper = _make_paper(var_session)
        spec = _make_spec(paper.id)
        _persist_base_record(var_session, paper.id, spec, tmp_path)

        svc = VariantService(var_session, tmp_path)
        result = svc.create_variant(
            base_strategy_id=spec.strategy_id,
            variant_name="loaded",
            description="desc",
            rationale="rat",
            added_rules=[_make_qf_rule()],
        )
        variant = svc.get_variant_spec(result.strategy_id)
        assert isinstance(variant, StrategyVariant)
        assert variant.variant_id == "loaded_v1"
        assert len(variant.added_rules) == 1

    def test_raises_on_base_record(self, var_session, tmp_path):
        paper = _make_paper(var_session)
        spec = _make_spec(paper.id)
        _persist_base_record(var_session, paper.id, spec, tmp_path)

        svc = VariantService(var_session, tmp_path)
        with pytest.raises(VariantError, match="base spec"):
            svc.get_variant_spec(spec.strategy_id)

    def test_raises_when_not_found(self, var_session, tmp_path):
        svc = VariantService(var_session, tmp_path)
        with pytest.raises(VariantError, match="not found"):
            svc.get_variant_spec(uuid.uuid4())
