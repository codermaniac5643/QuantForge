# src/quantforge/strategy/writer.py
"""
Spec Writer service — Milestone 14.

Takes the in-memory PipelineResult produced by the extraction pipeline
and:
  1. Runs deterministic post-extraction provenance checks.
  2. Assigns rule_ids where missing.
  3. Writes the StrategySpecification JSON to disk (atomic write).
  4. Creates a StrategyRecord in the database.
  5. Supersedes any previous spec for the same (paper_id, variant).

The spec writer does NOT call the LLM. It only validates and assembles.
"""

from __future__ import annotations

import json
import logging
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from quantforge.database.models.strategy import StrategyRecord
from quantforge.database.repositories.strategy import StrategyRepository
from quantforge.strategy.extraction.pipeline import PipelineResult
from quantforge.strategy.models.enums import (
    ExtractionMethod,
    ProvenanceLabel,
    StrategyStatus,
)
from quantforge.strategy.models.spec import (
    PillarData,
    RuleObject,
    StrategySpecification,
)

logger = logging.getLogger(__name__)

ALL_PILLARS = [
    "market_regime", "market_eligibility", "asset_selection",
    "entry_signal", "position_sizing", "risk_management",
    "exit", "portfolio", "backtesting",
]


# ---------------------------------------------------------------------------
# Post-extraction provenance checks
# ---------------------------------------------------------------------------


class ProvenanceCheckError(Exception):
    """Raised when a spec fails a post-extraction provenance check."""


def check_provenance(spec: StrategySpecification) -> None:
    """
    Deterministic post-extraction provenance checks.

    Raises ProvenanceCheckError with a full description of all violations.
    Does NOT silently save a broken spec.

    Checks:
    1. Every rule has a provenance label.
    2. Every 'inferred' rule has an 'evidence' field.
    3. No 'quantforge_added' rule appears in the base spec.
    """
    violations: list[str] = []

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

    for pillar_name, pillar_data in pillar_map.items():
        for rule in pillar_data.rules:
            prefix = f"{pillar_name}/{rule.rule_id}"

            # Check 1: provenance label present (Pydantic already enforces type,
            # but guard against empty strings if rule_id was set manually)
            if not rule.provenance:
                violations.append(
                    f"{prefix}: missing provenance label"
                )

            # Check 2: inferred rules need evidence
            elif rule.provenance == ProvenanceLabel.INFERRED and not rule.evidence:
                violations.append(
                    f"{prefix}: provenance='inferred' but 'evidence' is missing"
                )

            # Check 3: quantforge_added must not appear in base spec
            if (
                spec.variant == "base"
                and rule.provenance == ProvenanceLabel.QUANTFORGE_ADDED
            ):
                violations.append(
                    f"{prefix}: 'quantforge_added' rule found in base spec "
                    f"(only allowed in variants)"
                )

    if violations:
        lines = "\n  ".join(violations)
        raise ProvenanceCheckError(
            f"Post-extraction provenance check failed "
            f"({len(violations)} violation(s)):\n  {lines}"
        )


# ---------------------------------------------------------------------------
# Rule ID assignment
# ---------------------------------------------------------------------------


def assign_rule_ids(spec: StrategySpecification) -> StrategySpecification:
    """
    Assign deterministic rule_ids to any rule that has an empty or
    placeholder id.  Format: <PILLAR_ABBREV>-<counter>.

    Returns the same spec object (mutated in-place via model_copy).
    Does not trigger re-validation — only fills missing ids.
    """
    ABBREV = {
        "market_regime": "MR",
        "market_eligibility": "ME",
        "asset_selection": "AS",
        "entry_signal": "ES",
        "position_sizing": "PS",
        "risk_management": "RM",
        "exit": "EX",
        "portfolio": "PO",
        "backtesting": "BT",
    }

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

    for pillar_name, pillar_data in pillar_map.items():
        abbrev = ABBREV.get(pillar_name, pillar_name[:2].upper())
        for i, rule in enumerate(pillar_data.rules, start=1):
            if not rule.rule_id or rule.rule_id.strip() in ("", "null", "None"):
                # Pydantic models are immutable by default — rebuild the list
                new_rule = rule.model_copy(update={"rule_id": f"{abbrev}-{i:03d}"})
                pillar_data.rules[i - 1] = new_rule

    return spec


# ---------------------------------------------------------------------------
# Atomic JSON file writer
# ---------------------------------------------------------------------------


def _write_spec_json(spec: StrategySpecification, path: Path) -> None:
    """
    Write spec JSON atomically: write to a temp file then rename.
    Ensures the directory exists first.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    # Use model_dump with mode='json' so UUID/datetime serialize correctly
    data = spec.model_dump(mode="json")

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        suffix=".tmp",
        delete=False,
    ) as tmp:
        json.dump(data, tmp, indent=2, ensure_ascii=False)
        tmp_path = Path(tmp.name)

    tmp_path.replace(path)
    logger.info("Spec written atomically to %s", path)


# ---------------------------------------------------------------------------
# Write result
# ---------------------------------------------------------------------------


class WriteResult:
    """Outcome of a spec write operation."""

    def __init__(
        self,
        success: bool,
        strategy_id: uuid.UUID | None = None,
        version: int | None = None,
        file_path: Path | None = None,
        error: str | None = None,
    ) -> None:
        self.success = success
        self.strategy_id = strategy_id
        self.version = version
        self.file_path = file_path
        self.error = error

    def __repr__(self) -> str:
        return (
            f"WriteResult(success={self.success}, "
            f"strategy_id={self.strategy_id}, "
            f"version={self.version}, "
            f"file_path={self.file_path})"
        )


# ---------------------------------------------------------------------------
# SpecWriter service
# ---------------------------------------------------------------------------


class SpecWriter:
    """
    Validates, persists, and versions a StrategySpecification.

    Usage:
        writer = SpecWriter(session=session, storage_root=Path("research_library"))
        result = writer.write(pipeline_result)
    """

    def __init__(
        self,
        session: Session,
        storage_root: Path,
        variant: str = "base",
    ) -> None:
        self.session = session
        self.storage_root = storage_root
        self.variant = variant
        self._repo = StrategyRepository(session)

    def write(self, pipeline_result: PipelineResult) -> WriteResult:
        """
        Validate the spec from a PipelineResult and persist it.

        Steps:
        1. Guard: spec must be present.
        2. Assign missing rule_ids.
        3. Run post-extraction provenance checks.
        4. Determine version (next_version for this paper+variant).
        5. Stamp extraction metadata on the spec.
        6. Write JSON atomically to disk.
        7. Create StrategyRecord in DB.
        8. Supersede any previous draft/under_review/approved record.

        Returns WriteResult. On provenance failure, raises ProvenanceCheckError
        (caller decides whether to save as rejected or surface the error).
        """
        if pipeline_result.spec is None:
            return WriteResult(
                success=False,
                error="PipelineResult has no spec to write.",
            )

        spec = pipeline_result.spec

        # Step 1: assign missing rule_ids
        spec = assign_rule_ids(spec)

        # Step 2: post-extraction provenance checks (raises on violation)
        check_provenance(spec)

        # Step 3: determine version
        paper_id = pipeline_result.paper_id
        version = self._repo.next_version(paper_id, variant=self.variant)

        # Step 4: stamp version and updated_at on the spec
        now_iso = datetime.now(timezone.utc).isoformat()
        spec = spec.model_copy(update={
            "version": version,
            "variant": self.variant,
            "updated_at": now_iso,
        })

        # Step 5: determine output path
        spec_filename = f"spec_v{version}.json"
        spec_path = (
            self.storage_root
            / str(paper_id)
            / "strategy"
            / spec_filename
        )

        # Step 6: write JSON atomically
        _write_spec_json(spec, spec_path)

        # Step 7: determine status
        # Partial extractions stay as draft; complete ones move to under_review
        # so the review workflow can pick them up immediately.
        status = (
            StrategyStatus.DRAFT
            if pipeline_result.is_partial
            else StrategyStatus.UNDER_REVIEW
        )

        # Step 8: create DB record
        record = StrategyRecord(
            id=spec.strategy_id,
            paper_id=paper_id,
            version=version,
            variant=self.variant,
            status=status.value,
            completeness_class=spec.completeness_class.value,
            file_path=str(spec_path),
            extracted_by=ExtractionMethod.LLM_ASSISTED.value,
        )
        self._repo.create(record)

        # Step 9: supersede previous records for this paper+variant
        self._repo.supersede_previous(
            paper_id=paper_id,
            variant=self.variant,
            keep_id=spec.strategy_id,
        )

        logger.info(
            "SpecWriter: wrote spec %s v%d for paper %s → %s (status=%s)",
            spec.strategy_id, version, paper_id, spec_path, status.value,
        )

        return WriteResult(
            success=True,
            strategy_id=spec.strategy_id,
            version=version,
            file_path=spec_path,
        )

    def write_draft_on_failure(
        self,
        pipeline_result: PipelineResult,
        error: str,
    ) -> WriteResult:
        """
        Write a minimal draft record when extraction fails (no spec JSON).

        Creates a StrategyRecord with status=draft and no file_path,
        so the failure is traceable in the DB without a broken spec file.
        """
        paper_id = pipeline_result.paper_id
        version = self._repo.next_version(paper_id, variant=self.variant)
        strategy_id = uuid.uuid4()

        record = StrategyRecord(
            id=strategy_id,
            paper_id=paper_id,
            version=version,
            variant=self.variant,
            status=StrategyStatus.DRAFT.value,
            completeness_class="research_only",
            file_path=None,
            extracted_by=ExtractionMethod.LLM_ASSISTED.value,
        )
        self._repo.create(record)

        logger.warning(
            "SpecWriter: saved failure draft for paper %s v%d — %s",
            paper_id, version, error,
        )

        return WriteResult(
            success=False,
            strategy_id=strategy_id,
            version=version,
            error=error,
        )
