# tests/strategy/test_review.py
"""
Tests for the Review service — Milestone 15.

Coverage:
- run_checklist: each of the six check points (pass and fail cases)
- ReviewService.list_pending_review
- ReviewService.get_review_summary (spec present / missing file / no file_path)
- ReviewService.approve_spec: checklist pass → approved; checklist fail → ValueError
- ReviewService.reject_spec: with notes succeeds; empty notes raises; wrong status raises
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
from quantforge.database.repositories.strategy import (
    InvalidStatusTransitionError,
    StrategyRepository,
)
from quantforge.models.states import PaperState
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
from quantforge.strategy.models.spec import (
    ComponentCoverageMap,
    MethodologyAssessment,
    MissingComponent,
    PillarData,
    RuleObject,
    StrategyPillars,
    StrategySpecification,
)
from quantforge.strategy.review import (
    ReviewService,
    ChecklistItem,
    run_checklist,
)

TEST_DB_URL = "postgresql://postgres:postgres@localhost:5432/quantforge_test"


# ---------------------------------------------------------------------------
# DB fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def review_engine():
    engine = create_engine(TEST_DB_URL)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(scope="function")
def review_session(review_engine):
    connection = review_engine.connect()
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
        authors=[],
        keywords=[],
        sources=[],
        state=PaperState.PROCESSED.value,
    )
    session.add(paper)
    session.flush()
    return paper


def _make_rule(
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


def _make_good_spec(paper_id: uuid.UUID | None = None) -> StrategySpecification:
    """A spec that passes all six checklist items."""
    pid = paper_id or uuid.uuid4()
    return StrategySpecification(
        paper_id=pid,
        extracted_by=ExtractionMethod.LLM_ASSISTED,
        completeness_class=CompletenessClass.ENTRY_FOCUSED,
        component_coverage=ComponentCoverageMap(entry_signal=ComponentCoverage.PRESENT),
        pillars=StrategyPillars(
            entry_signal=PillarData(present=True, rules=[_make_rule()]),
        ),
        missing_components=[
            MissingComponent(
                pillar=p,
                status="absent",
                impact=MissingImpact.MODERATE,
                description=f"{p} not found.",
                implication="Cannot implement without it.",
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
            primary_weakness="No exit defined.",
            key_missing_components=["exit"],
            reason="Entry fully specified; exit absent.",
        ),
    )


def _persist_spec_and_record(
    session,
    paper_id: uuid.UUID,
    spec: StrategySpecification,
    tmp_path: Path,
    status: StrategyStatus = StrategyStatus.UNDER_REVIEW,
) -> StrategyRecord:
    """Write spec JSON to disk and create a StrategyRecord."""
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
# run_checklist — unit tests (no DB)
# ---------------------------------------------------------------------------


class TestRunChecklist:
    def test_all_pass_on_good_spec(self):
        spec = _make_good_spec()
        result = run_checklist(spec)
        assert result.passed
        assert len(result.items) == 6
        assert not result.failures

    def test_check1_fails_when_no_provenance(self):
        spec = _make_good_spec()
        bad = RuleObject.model_construct(
            rule_id="R999", description="d", provenance=None, source_ref="s"
        )
        spec.pillars.entry_signal.rules = [bad]
        result = run_checklist(spec)
        assert not result.items[0].passed   # check 1
        assert result.items[0].number == 1

    def test_check2_fails_on_inferred_without_evidence(self):
        spec = _make_good_spec()
        bad = RuleObject.model_construct(
            rule_id="R002",
            description="inferred",
            provenance=ProvenanceLabel.INFERRED,
            source_ref="s",
            evidence=None,
        )
        spec.pillars.entry_signal.rules = [bad]
        result = run_checklist(spec)
        assert not result.items[1].passed   # check 2
        assert "R002" in result.items[1].detail

    def test_check2_passes_when_inferred_has_evidence(self):
        spec = _make_good_spec()
        rule = _make_rule(
            rule_id="R002",
            provenance=ProvenanceLabel.INFERRED,
            evidence="Implied by Table 2.",
        )
        spec.pillars.entry_signal.rules = [rule]
        result = run_checklist(spec)
        assert result.items[1].passed

    def test_check3_fails_on_qf_added_in_base(self):
        spec = _make_good_spec()
        bad = RuleObject.model_construct(
            rule_id="QF001",
            description="qf",
            provenance=ProvenanceLabel.QUANTFORGE_ADDED,
            source_ref="s",
        )
        spec.pillars.entry_signal.rules = [bad]
        result = run_checklist(spec)
        assert not result.items[2].passed

    def test_check3_passes_for_non_base_variant(self):
        spec = _make_good_spec()
        spec = spec.model_copy(update={"variant": "enhanced"})
        bad = RuleObject.model_construct(
            rule_id="QF001",
            description="qf",
            provenance=ProvenanceLabel.QUANTFORGE_ADDED,
            source_ref="s",
        )
        spec.pillars.entry_signal.rules = [bad]
        result = run_checklist(spec)
        assert result.items[2].passed

    def test_check4_fails_when_absent_pillar_not_recorded(self):
        spec = _make_good_spec()
        # Remove all missing_components — absent pillars go unrecorded
        spec = spec.model_copy(update={"missing_components": []})
        result = run_checklist(spec)
        assert not result.items[3].passed

    def test_check4_passes_when_all_absent_recorded(self):
        spec = _make_good_spec()   # already has all absent pillars in missing_components
        result = run_checklist(spec)
        assert result.items[3].passed

    def test_check5_fails_complete_strategy_with_missing_core(self):
        spec = _make_good_spec()
        spec = spec.model_copy(update={"completeness_class": CompletenessClass.COMPLETE_STRATEGY})
        # entry_signal is present, but exit/sizing/risk are absent
        result = run_checklist(spec)
        assert not result.items[4].passed
        assert "complete_strategy" in result.items[4].detail

    def test_check5_fails_research_only_with_rules(self):
        spec = _make_good_spec()
        spec = spec.model_copy(update={"completeness_class": CompletenessClass.RESEARCH_ONLY})
        result = run_checklist(spec)
        assert not result.items[4].passed

    def test_check5_passes_entry_focused_with_entry(self):
        spec = _make_good_spec()   # entry_focused + entry_signal present
        result = run_checklist(spec)
        assert result.items[4].passed

    def test_check6_fails_when_assessment_absent(self):
        spec = _make_good_spec()
        spec = spec.model_copy(update={"methodology_assessment": None})
        result = run_checklist(spec)
        assert not result.items[5].passed

    def test_check6_fails_when_assessment_incomplete(self):
        spec = _make_good_spec()
        bad_ma = MethodologyAssessment(
            implementability=Implementability.RESEARCH_ONLY,
            suitable_for_backtest=False,
            primary_strength="",   # empty → incomplete
            primary_weakness="weakness",
            reason="reason",
        )
        spec = spec.model_copy(update={"methodology_assessment": bad_ma})
        result = run_checklist(spec)
        assert not result.items[5].passed

    def test_failures_property_returns_only_failed(self):
        spec = _make_good_spec()
        spec = spec.model_copy(update={"methodology_assessment": None})
        result = run_checklist(spec)
        assert len(result.failures) == 1
        assert result.failures[0].number == 6


# ---------------------------------------------------------------------------
# ReviewService — integration tests
# ---------------------------------------------------------------------------


class TestReviewServiceListPending:
    def test_empty_when_none_under_review(self, review_session):
        service = ReviewService(review_session)
        assert service.list_pending_review() == []

    def test_returns_under_review_records(self, review_session, tmp_path):
        paper = _make_paper(review_session)
        spec = _make_good_spec(paper_id=paper.id)
        _persist_spec_and_record(
            review_session, paper.id, spec, tmp_path, StrategyStatus.UNDER_REVIEW
        )
        service = ReviewService(review_session)
        results = service.list_pending_review()
        assert len(results) == 1
        assert results[0].status == StrategyStatus.UNDER_REVIEW.value

    def test_does_not_return_draft_or_approved(self, review_session, tmp_path):
        paper = _make_paper(review_session)
        spec = _make_good_spec(paper_id=paper.id)
        _persist_spec_and_record(
            review_session, paper.id, spec, tmp_path, StrategyStatus.DRAFT
        )
        service = ReviewService(review_session)
        assert service.list_pending_review() == []


class TestReviewServiceGetSummary:
    def test_loads_spec_and_runs_checklist(self, review_session, tmp_path):
        paper = _make_paper(review_session)
        spec = _make_good_spec(paper_id=paper.id)
        _persist_spec_and_record(review_session, paper.id, spec, tmp_path)

        service = ReviewService(review_session)
        summary = service.get_review_summary(spec.strategy_id)

        assert summary.spec is not None
        assert summary.load_error is None
        assert len(summary.checklist.items) == 6

    def test_missing_file_sets_load_error(self, review_session):
        paper = _make_paper(review_session)
        record = StrategyRecord(
            id=uuid.uuid4(),
            paper_id=paper.id,
            version=1,
            variant="base",
            status=StrategyStatus.UNDER_REVIEW.value,
            completeness_class=CompletenessClass.ENTRY_FOCUSED.value,
            file_path="/nonexistent/path/spec_v1.json",
            extracted_by=ExtractionMethod.LLM_ASSISTED.value,
        )
        review_session.add(record)
        review_session.flush()

        service = ReviewService(review_session)
        summary = service.get_review_summary(record.id)
        assert summary.load_error is not None
        assert summary.spec is None

    def test_no_file_path_sets_load_error(self, review_session):
        paper = _make_paper(review_session)
        record = StrategyRecord(
            id=uuid.uuid4(),
            paper_id=paper.id,
            version=1,
            variant="base",
            status=StrategyStatus.DRAFT.value,
            completeness_class=CompletenessClass.RESEARCH_ONLY.value,
            file_path=None,
            extracted_by=ExtractionMethod.LLM_ASSISTED.value,
        )
        review_session.add(record)
        review_session.flush()

        service = ReviewService(review_session)
        summary = service.get_review_summary(record.id)
        assert summary.load_error is not None

    def test_raises_when_not_found(self, review_session):
        service = ReviewService(review_session)
        with pytest.raises(ValueError, match="not found"):
            service.get_review_summary(uuid.uuid4())


class TestReviewServiceApprove:
    def test_approve_good_spec_sets_approved(self, review_session, tmp_path):
        paper = _make_paper(review_session)
        spec = _make_good_spec(paper_id=paper.id)
        _persist_spec_and_record(review_session, paper.id, spec, tmp_path)

        service = ReviewService(review_session)
        record = service.approve_spec(spec.strategy_id, reviewer_id="analyst_01")
        assert record.status == StrategyStatus.APPROVED.value
        assert record.reviewed_by == "analyst_01"

    def test_approve_fails_when_checklist_fails(self, review_session, tmp_path):
        paper = _make_paper(review_session)
        spec = _make_good_spec(paper_id=paper.id)
        # Break check 6 — remove methodology assessment
        bad_spec = spec.model_copy(update={"methodology_assessment": None})
        _persist_spec_and_record(review_session, paper.id, bad_spec, tmp_path)

        service = ReviewService(review_session)
        with pytest.raises(ValueError, match="checklist"):
            service.approve_spec(bad_spec.strategy_id, reviewer_id="analyst_01")

    def test_approve_fails_on_wrong_status(self, review_session, tmp_path):
        paper = _make_paper(review_session)
        spec = _make_good_spec(paper_id=paper.id)
        _persist_spec_and_record(
            review_session, paper.id, spec, tmp_path, StrategyStatus.DRAFT
        )
        service = ReviewService(review_session)
        with pytest.raises((ValueError, InvalidStatusTransitionError)):
            service.approve_spec(spec.strategy_id, reviewer_id="analyst_01")


class TestReviewServiceReject:
    def test_reject_with_notes_succeeds(self, review_session, tmp_path):
        paper = _make_paper(review_session)
        spec = _make_good_spec(paper_id=paper.id)
        _persist_spec_and_record(review_session, paper.id, spec, tmp_path)

        service = ReviewService(review_session)
        record = service.reject_spec(
            spec.strategy_id,
            reviewer_id="analyst_02",
            notes="Exit rules are missing — cannot approve.",
        )
        assert record.status == StrategyStatus.REJECTED.value
        assert record.reviewed_by == "analyst_02"

    def test_reject_empty_notes_raises(self, review_session, tmp_path):
        paper = _make_paper(review_session)
        spec = _make_good_spec(paper_id=paper.id)
        _persist_spec_and_record(review_session, paper.id, spec, tmp_path)

        service = ReviewService(review_session)
        with pytest.raises(ValueError, match="non-empty"):
            service.reject_spec(spec.strategy_id, reviewer_id="analyst_02", notes="")

    def test_reject_whitespace_notes_raises(self, review_session, tmp_path):
        paper = _make_paper(review_session)
        spec = _make_good_spec(paper_id=paper.id)
        _persist_spec_and_record(review_session, paper.id, spec, tmp_path)

        service = ReviewService(review_session)
        with pytest.raises(ValueError, match="non-empty"):
            service.reject_spec(spec.strategy_id, reviewer_id="analyst_02", notes="   ")

    def test_reject_approved_raises_bad_transition(self, review_session, tmp_path):
        """APPROVED → REJECTED is not a valid transition; only SUPERSEDED is."""
        paper = _make_paper(review_session)
        spec = _make_good_spec(paper_id=paper.id)
        _persist_spec_and_record(
            review_session, paper.id, spec, tmp_path, StrategyStatus.APPROVED
        )
        service = ReviewService(review_session)
        with pytest.raises(InvalidStatusTransitionError):
            service.reject_spec(
                spec.strategy_id,
                reviewer_id="analyst_02",
                notes="Wrong spec approved.",
            )
