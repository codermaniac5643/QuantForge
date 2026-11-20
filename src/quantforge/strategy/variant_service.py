# src/quantforge/strategy/variant_service.py
"""
Variant Management service — Milestone 16.

A variant extends an approved base spec with `quantforge_added` rules
without modifying the base spec itself.

Key invariants:
- Source base spec must be APPROVED.
- added_rules must all carry provenance='quantforge_added'.
- Base spec JSON file is never touched.
- Variant JSON is written to strategy/variants/<variant_name>_v<N>.json.
- A StrategyRecord with variant=<variant_name> is created in the DB.
"""

from __future__ import annotations

import json
import logging
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import Session

from quantforge.database.models.strategy import StrategyRecord
from quantforge.database.repositories.strategy import StrategyRepository
from quantforge.strategy.models.enums import (
    ExtractionMethod,
    ProvenanceLabel,
    StrategyStatus,
)
from quantforge.strategy.models.spec import (
    RuleObject,
    StrategySpecification,
    StrategyVariant,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class VariantError(Exception):
    """Raised when a variant operation violates an invariant."""


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_added_rules(added_rules: list[RuleObject]) -> None:
    """
    Every rule in added_rules must carry provenance='quantforge_added'.
    Raises VariantError listing any violations.
    """
    bad = [
        r.rule_id
        for r in added_rules
        if r.provenance != ProvenanceLabel.QUANTFORGE_ADDED
    ]
    if bad:
        raise VariantError(
            f"All added_rules in a variant must have provenance='quantforge_added'. "
            f"Bad rule_ids: {bad}"
        )


def _load_spec(file_path: str | None) -> StrategySpecification:
    """Load a StrategySpecification from its JSON file path."""
    if not file_path:
        raise VariantError("Base spec record has no file_path — cannot load spec.")
    path = Path(file_path)
    if not path.exists():
        raise VariantError(f"Base spec file not found: {file_path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return StrategySpecification(**data)
    except Exception as e:
        raise VariantError(f"Could not parse base spec JSON: {e}") from e


# ---------------------------------------------------------------------------
# Write result
# ---------------------------------------------------------------------------


class VariantWriteResult:
    def __init__(
        self,
        strategy_id: uuid.UUID,
        variant_name: str,
        version: int,
        file_path: Path,
    ) -> None:
        self.strategy_id = strategy_id
        self.variant_name = variant_name
        self.version = version
        self.file_path = file_path

    def __repr__(self) -> str:
        return (
            f"VariantWriteResult(strategy_id={self.strategy_id}, "
            f"variant={self.variant_name!r}, version={self.version}, "
            f"file_path={self.file_path})"
        )


# ---------------------------------------------------------------------------
# Atomic writer
# ---------------------------------------------------------------------------


def _write_variant_json(variant: StrategyVariant, path: Path) -> None:
    """Write variant JSON atomically: temp file → rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = variant.model_dump(mode="json")
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
    logger.info("Variant written atomically to %s", path)


# ---------------------------------------------------------------------------
# VariantService
# ---------------------------------------------------------------------------


class VariantService:
    """
    All variant management operations.
    Session lifecycle is managed by the caller.
    """

    def __init__(self, session: Session, storage_root: Path) -> None:
        self.session = session
        self.storage_root = storage_root
        self._repo = StrategyRepository(session)

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create_variant(
        self,
        base_strategy_id: UUID,
        variant_name: str,
        description: str,
        rationale: str,
        added_rules: list[RuleObject],
    ) -> VariantWriteResult:
        """
        Create a named variant from an approved base spec.

        Args:
            base_strategy_id: UUID of the APPROVED base StrategyRecord.
            variant_name:      Short name for the variant (e.g. "with_exit_rules").
                               Used as the variant field in DB and in the filename.
            description:       Human-readable description of what the variant adds.
            rationale:         Why these rules were added (from AGENTS.md scope).
            added_rules:       Rules to add, all must be provenance='quantforge_added'.

        Returns:
            VariantWriteResult with file_path and strategy_id.

        Raises:
            VariantError: on any invariant violation.
        """
        if not variant_name or not variant_name.strip():
            raise VariantError("variant_name must be a non-empty string.")

        # Reject reserved name
        if variant_name.strip() == "base":
            raise VariantError("'base' is reserved; choose a different variant name.")

        # Load base record
        base_record = self._repo.get_by_id(base_strategy_id)
        if base_record is None:
            raise VariantError(f"Base strategy {base_strategy_id} not found.")
        if base_record.status != StrategyStatus.APPROVED.value:
            raise VariantError(
                f"Can only create variants from APPROVED specs. "
                f"Base spec {base_strategy_id} has status={base_record.status!r}."
            )
        if base_record.variant != "base":
            raise VariantError(
                "Can only create variants from a base spec (variant='base'). "
                f"Got variant={base_record.variant!r}."
            )

        # Validate added_rules provenance
        _validate_added_rules(added_rules)

        # Load base spec JSON (read-only — we do not modify it)
        base_spec = _load_spec(base_record.file_path)

        # Determine version for this variant name
        version = self._repo.next_version(
            paper_id=base_record.paper_id,
            variant=variant_name,
        )

        # Build StrategyVariant model
        variant_id = f"{variant_name}_v{version}"
        variant_obj = StrategyVariant(
            variant_id=variant_id,
            based_on_strategy_id=base_strategy_id,
            based_on_version=base_record.version,
            description=description,
            rationale=rationale,
            added_rules=added_rules,
            modified_rules=[],
        )

        # Build output path: strategy/variants/<variant_name>_v<N>.json
        variants_dir = (
            self.storage_root
            / str(base_record.paper_id)
            / "strategy"
            / "variants"
        )
        file_path = variants_dir / f"{variant_id}.json"

        # Write atomically
        _write_variant_json(variant_obj, file_path)

        # Create DB record (reuses strategy_specifications, variant field != "base")
        strategy_id = uuid.uuid4()
        record = StrategyRecord(
            id=strategy_id,
            paper_id=base_record.paper_id,
            version=version,
            variant=variant_name,
            status=StrategyStatus.DRAFT.value,
            completeness_class=base_record.completeness_class,
            file_path=str(file_path),
            extracted_by=ExtractionMethod.LLM_ASSISTED.value,
        )
        self._repo.create(record)

        logger.info(
            "VariantService: created variant %r v%d for paper %s → %s",
            variant_name, version, base_record.paper_id, file_path,
        )

        return VariantWriteResult(
            strategy_id=strategy_id,
            variant_name=variant_name,
            version=version,
            file_path=file_path,
        )

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def list_variants(self, paper_id: UUID) -> list[StrategyRecord]:
        """Return all non-base strategy records for a paper."""
        from sqlalchemy import select as _select
        from quantforge.database.models.strategy import StrategyRecord as SR

        stmt = (
            _select(SR)
            .where(
                SR.paper_id == paper_id,
                SR.variant != "base",
            )
            .order_by(SR.variant.asc(), SR.version.asc())
        )
        return list(self.session.scalars(stmt))

    def get_variant_spec(self, strategy_id: UUID) -> StrategyVariant:
        """
        Load and return the StrategyVariant object for a variant record.

        Raises:
            VariantError: record not found, not a variant, or file missing.
        """
        record = self._repo.get_by_id(strategy_id)
        if record is None:
            raise VariantError(f"Strategy record {strategy_id} not found.")
        if record.variant == "base":
            raise VariantError(
                f"Record {strategy_id} is a base spec, not a variant."
            )
        if not record.file_path or not Path(record.file_path).exists():
            raise VariantError(
                f"Variant file not found: {record.file_path}"
            )
        try:
            data = json.loads(Path(record.file_path).read_text(encoding="utf-8"))
            return StrategyVariant(**data)
        except Exception as e:
            raise VariantError(f"Could not parse variant JSON: {e}") from e

    def get_base_spec(self, strategy_id: UUID) -> StrategySpecification:
        """
        Given a variant record's strategy_id, load the base spec it was
        derived from. Convenience for the CLI 'show' command.
        """
        record = self._repo.get_by_id(strategy_id)
        if record is None:
            raise VariantError(f"Strategy record {strategy_id} not found.")
        variant_spec = self.get_variant_spec(strategy_id)
        base_record = self._repo.get_by_id(variant_spec.based_on_strategy_id)
        if base_record is None:
            raise VariantError(
                f"Base record {variant_spec.based_on_strategy_id} not found."
            )
        return _load_spec(base_record.file_path)
