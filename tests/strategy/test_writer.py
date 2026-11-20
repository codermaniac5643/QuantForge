# tests/strategy/test_writer.py
"""
Tests for the Spec Writer service — Milestone 14.

Coverage:
- Provenance checks (all pass / each failure mode)
- Rule ID assignment
- Spec JSON file written atomically to correct path
- DB record created with correct fields
- Status: under_review for complete, draft for partial
- Version incrementing
- Supersede previous records
- write_draft_on_failure creates DB record without file
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from quantforge.database.base import Base
from quantforge.database.models.paper import PaperRecord  # noqa: F401 — FK dependency
from quantforge.database.models.strategy import StrategyRecord
from quantforge.database.repositories.strategy import StrategyRepository
from quantforge.models.states import PaperState
from quantforge.strategy.extraction.pipeline import PipelineResult
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
    ExtractionAudit,
    MethodologyAssessment,
    MissingComponent,
    PillarData,
    RuleObject,
    StrategyPillars,
    StrategySpecification,
)
from quantforge.strategy.writer import (
    ProvenanceCheckError,
    SpecWriter,
    WriteResult,
    assign_rule_ids,
    check_provenance,
)

TEST_DB_URL = "postgresql://postgres:postgres@localhost:5432/quantforge_test"


# ---------------------------------------------------------------------------
# DB fixtures (same transactional pattern as test_repository.py)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def writer_engine():
    engine = create_engine(TEST_DB_URL)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(scope="function")
def writer_session(writer_engine):
    connection = writer_engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection)
    session = Session()
    yield session
    session.close()
    transaction.rollback()
    connection.close()


# ---------------------------------------------------------------------------
# Spec / pipeline helpers
# ---------------------------------------------------------------------------


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


def _make_spec(
    paper_id: uuid.UUID | None = None,
    rules: list[RuleObject] | None = None,
    variant: str = "base",
) -> StrategySpecification:
    paper_id = paper_id or uuid.uuid4()
    entry_signal = PillarData(
        present=True,
        source_note="Section 3",
        rules=rules or [_make_rule()],
    )
    return StrategySpecification(
        paper_id=paper_id,
        variant=variant,
        extracted_by=ExtractionMethod.LLM_ASSISTED,
        completeness_class=CompletenessClass.ENTRY_FOCUSED,
        pillars=StrategyPillars(entry_signal=entry_signal),
        methodology_assessment=MethodologyAssessment(
            implementability=Implementability.REQUIRES_ADDITIONS,
            suitable_for_backtest=True,
            primary_strength="Clear entry rules",
            primary_weakness="No exit defined",
            key_missing_components=["exit"],
            reason="Entry fully specified; exit absent.",
        ),
    )




def _make_paper(session) -> PaperRecord:
    """Insert a minimal PaperRecord to satisfy FK on strategy_specifications."""
    paper = PaperRecord(
        id=uuid.uuid4(),
        title="Test Paper",
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


# ---------------------------------------------------------------------------
# ProvenanceCheckError tests (pure unit — no DB)
# ---------------------------------------------------------------------------


class TestCheckProvenance:
    def test_valid_explicit_rule_passes(self):
        spec = _make_spec(rules=[_make_rule(provenance=ProvenanceLabel.EXPLICIT)])
        check_provenance(spec)  # must not raise

    def test_valid_inferred_with_evidence_passes(self):
        spec = _make_spec(rules=[
            _make_rule(
                provenance=ProvenanceLabel.INFERRED,
                evidence="Implied by Table 2 results.",
            )
        ])
        check_provenance(spec)  # must not raise

    def test_inferred_missing_evidence_fails(self):
        # Use model_construct to bypass Pydantic's own validator so we can
        # test that check_provenance (the writer-level check) catches this.
        bad_rule = RuleObject.model_construct(
            rule_id="R001",
            description="inferred without evidence",
            provenance=ProvenanceLabel.INFERRED,
            source_ref="Section 1",
            evidence=None,
        )
        spec = _make_spec()
        spec.pillars.entry_signal.rules = [bad_rule]
        with pytest.raises(ProvenanceCheckError, match="inferred"):
            check_provenance(spec)

    def test_quantforge_added_in_base_fails(self):
        rule = _make_rule(provenance=ProvenanceLabel.QUANTFORGE_ADDED)
        spec = _make_spec()
        spec.pillars.entry_signal.rules = [rule]
        with pytest.raises(ProvenanceCheckError, match="quantforge_added"):
            check_provenance(spec)

    def test_quantforge_added_in_variant_passes(self):
        rule = _make_rule(provenance=ProvenanceLabel.QUANTFORGE_ADDED)
        spec = _make_spec(variant="enhanced", rules=[rule])
        check_provenance(spec)  # must not raise

    def test_multiple_violations_reported_together(self):
        rule1 = _make_rule(rule_id="R001", provenance=ProvenanceLabel.QUANTFORGE_ADDED)
        # Use model_construct to bypass Pydantic validation and inject a bad rule
        rule2 = RuleObject.model_construct(
            rule_id="R002",
            description="bad inferred rule",
            provenance=ProvenanceLabel.INFERRED,
            source_ref="Section 1",
            evidence=None,  # deliberately missing
        )
        spec = _make_spec()
        spec.pillars.entry_signal.rules = [rule1, rule2]
        with pytest.raises(ProvenanceCheckError) as exc:
            check_provenance(spec)
        msg = str(exc.value)
        assert "2 violation" in msg


# ---------------------------------------------------------------------------
# assign_rule_ids tests (pure unit)
# ---------------------------------------------------------------------------


class TestAssignRuleIds:
    def test_existing_ids_unchanged(self):
        spec = _make_spec(rules=[_make_rule(rule_id="MY-001")])
        result = assign_rule_ids(spec)
        assert result.pillars.entry_signal.rules[0].rule_id == "MY-001"

    def test_empty_id_gets_assigned(self):
        rule = _make_rule(rule_id="R001")
        rule = rule.model_copy(update={"rule_id": ""})
        spec = _make_spec(rules=[rule])
        result = assign_rule_ids(spec)
        assigned = result.pillars.entry_signal.rules[0].rule_id
        assert assigned != ""
        assert assigned.startswith("ES-")

    def test_multiple_rules_get_sequential_ids(self):
        rules = [
            _make_rule(rule_id="").model_copy(update={"rule_id": ""}),
            _make_rule(rule_id="").model_copy(update={"rule_id": ""}),
        ]
        spec = _make_spec(rules=rules)
        result = assign_rule_ids(spec)
        ids = [r.rule_id for r in result.pillars.entry_signal.rules]
        assert ids[0] == "ES-001"
        assert ids[1] == "ES-002"


# ---------------------------------------------------------------------------
# SpecWriter integration tests (require DB)
# ---------------------------------------------------------------------------


class TestSpecWriterWrite:
    def test_write_creates_file_and_db_record(self, writer_session, tmp_path):
        paper = _make_paper(writer_session)
        spec = _make_spec(paper_id=paper.id)
        pipeline_result = _make_pipeline_result(spec=spec)

        writer = SpecWriter(
            session=writer_session,
            storage_root=tmp_path,
        )
        result = writer.write(pipeline_result)

        assert result.success is True
        assert result.version == 1
        assert result.file_path is not None
        assert result.file_path.exists()

        # JSON on disk is valid
        data = json.loads(result.file_path.read_text())
        assert str(paper.id) in data["paper_id"]

        # DB record exists
        repo = StrategyRepository(writer_session)
        record = repo.get_by_id(result.strategy_id)
        assert record is not None
        assert record.version == 1
        assert record.file_path == str(result.file_path)

    def test_write_complete_spec_sets_under_review(self, writer_session, tmp_path):
        paper = _make_paper(writer_session)
        spec = _make_spec(paper_id=paper.id)
        pipeline_result = _make_pipeline_result(spec=spec, is_partial=False)

        writer = SpecWriter(session=writer_session, storage_root=tmp_path)
        result = writer.write(pipeline_result)

        repo = StrategyRepository(writer_session)
        record = repo.get_by_id(result.strategy_id)
        assert record.status == StrategyStatus.UNDER_REVIEW.value

    def test_write_partial_spec_stays_draft(self, writer_session, tmp_path):
        paper = _make_paper(writer_session)
        spec = _make_spec(paper_id=paper.id)
        pipeline_result = _make_pipeline_result(spec=spec, is_partial=True)

        writer = SpecWriter(session=writer_session, storage_root=tmp_path)
        result = writer.write(pipeline_result)

        repo = StrategyRepository(writer_session)
        record = repo.get_by_id(result.strategy_id)
        assert record.status == StrategyStatus.DRAFT.value

    def test_spec_path_is_correct_format(self, writer_session, tmp_path):
        paper = _make_paper(writer_session)
        spec = _make_spec(paper_id=paper.id)
        pipeline_result = _make_pipeline_result(spec=spec)

        writer = SpecWriter(session=writer_session, storage_root=tmp_path)
        result = writer.write(pipeline_result)

        expected = tmp_path / str(paper.id) / "strategy" / "spec_v1.json"
        assert result.file_path == expected

    def test_version_increments_on_second_write(self, writer_session, tmp_path):
        paper = _make_paper(writer_session)

        for expected_version in (1, 2):
            spec = _make_spec(paper_id=paper.id)
            pipeline_result = _make_pipeline_result(spec=spec)
            writer = SpecWriter(session=writer_session, storage_root=tmp_path)
            result = writer.write(pipeline_result)
            assert result.version == expected_version

    def test_second_write_supersedes_first(self, writer_session, tmp_path):
        paper = _make_paper(writer_session)
        repo = StrategyRepository(writer_session)

        # First write (will be superseded)
        spec1 = _make_spec(paper_id=paper.id)
        r1 = SpecWriter(session=writer_session, storage_root=tmp_path).write(
            _make_pipeline_result(spec=spec1)
        )

        # Promote first record to APPROVED so it's supersedeable
        repo.update_status(r1.strategy_id, StrategyStatus.APPROVED)

        # Second write
        spec2 = _make_spec(paper_id=paper.id)
        r2 = SpecWriter(session=writer_session, storage_root=tmp_path).write(
            _make_pipeline_result(spec=spec2)
        )
        assert r2.version == 2

        # First record should now be SUPERSEDED
        old = repo.get_by_id(r1.strategy_id)
        assert old.status == StrategyStatus.SUPERSEDED.value

    def test_provenance_error_raised_before_write(self, writer_session, tmp_path):
        """ProvenanceCheckError must prevent file and DB creation."""
        paper = _make_paper(writer_session)

        rule = _make_rule(provenance=ProvenanceLabel.QUANTFORGE_ADDED)
        spec = _make_spec(paper_id=paper.id)
        spec.pillars.entry_signal.rules = [rule]
        pipeline_result = _make_pipeline_result(spec=spec)

        writer = SpecWriter(session=writer_session, storage_root=tmp_path)
        with pytest.raises(ProvenanceCheckError):
            writer.write(pipeline_result)

        # No spec file on disk
        strategy_dir = tmp_path / str(paper.id) / "strategy"
        if strategy_dir.exists():
            assert not list(strategy_dir.glob("spec_v*.json"))

    def test_no_spec_returns_failure(self, writer_session, tmp_path):
        pipeline_result = _make_pipeline_result(spec=None, success=False)
        writer = SpecWriter(session=writer_session, storage_root=tmp_path)
        result = writer.write(pipeline_result)
        assert result.success is False
        assert "no spec" in result.error.lower()

    def test_strategy_subdir_created_automatically(self, writer_session, tmp_path):
        paper = _make_paper(writer_session)
        spec = _make_spec(paper_id=paper.id)
        pipeline_result = _make_pipeline_result(spec=spec)

        writer = SpecWriter(session=writer_session, storage_root=tmp_path)
        result = writer.write(pipeline_result)

        assert result.file_path.parent.name == "strategy"
        assert result.file_path.parent.exists()


class TestSpecWriterDraftOnFailure:
    def test_creates_db_record_without_file(self, writer_session, tmp_path):
        paper = _make_paper(writer_session)
        pipeline_result = _make_pipeline_result(paper_id=paper.id, spec=None, success=False)
        pipeline_result.paper_id = paper.id  # ensure paper_id is set

        writer = SpecWriter(session=writer_session, storage_root=tmp_path)
        result = writer.write_draft_on_failure(
            pipeline_result, error="All sections filtered."
        )

        assert result.success is False
        assert result.strategy_id is not None

        repo = StrategyRepository(writer_session)
        record = repo.get_by_id(result.strategy_id)
        assert record is not None
        assert record.status == StrategyStatus.DRAFT.value
        assert record.file_path is None


# ---------------------------------------------------------------------------
# Atomic write test (pure unit — no DB)
# ---------------------------------------------------------------------------


class TestAtomicWrite:
    def test_no_partial_file_on_success(self, tmp_path):
        """After write, no .tmp files should remain."""
        from quantforge.strategy.writer import _write_spec_json

        spec = _make_spec()
        out_path = tmp_path / "strategy" / "spec_v1.json"
        _write_spec_json(spec, out_path)

        assert out_path.exists()
        assert not list(tmp_path.glob("**/*.tmp"))

    def test_json_is_valid_and_complete(self, tmp_path):
        from quantforge.strategy.writer import _write_spec_json

        spec = _make_spec()
        out_path = tmp_path / "spec_v1.json"
        _write_spec_json(spec, out_path)

        data = json.loads(out_path.read_text())
        assert "strategy_id" in data
        assert "pillars" in data
        assert "entry_signal" in data["pillars"]


def _make_pipeline_result(
    spec: StrategySpecification | None = None,
    is_partial: bool = False,
    success: bool = True,
    paper_id: uuid.UUID | None = None,
) -> PipelineResult:
    pid = paper_id or (spec.paper_id if spec else uuid.uuid4())
    result = PipelineResult(paper_id=pid)
    result.spec = spec
    result.success = success
    result.is_partial = is_partial
    return result
