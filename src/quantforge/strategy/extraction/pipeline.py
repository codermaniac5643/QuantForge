# src/quantforge/strategy/extraction/pipeline.py

"""
Extraction pipeline orchestrator — Milestone 13.

Coordinates the three-call LLM extraction sequence:
  Call 1 — Section Router
  Call 2 — Pillar Extractor (per group)
  Call 3 — Validation and Assessment

Also handles:
  - Section quality filter (min_section_chars)
  - Formula quality detection
  - Full-text fallback when sections.json has no usable headings
  - Section size guard
  - Extraction audit assembly
  - Partial spec saving on budget exhaustion
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from quantforge.llm.budget import BudgetManager, BudgetExhaustedError
from quantforge.strategy.extraction.chunker import (
    FormulaQuality,
    Section,
    apply_size_guard,
    chunk_full_text,
    filter_sections,
    load_sections_from_package,
)
from quantforge.strategy.extraction.extractor import (
    DEFAULT_PILLAR_GROUPS,
    ExtractionResult,
    extract_all_groups,
)
from quantforge.strategy.extraction.router import RoutingResult, route_sections
from quantforge.strategy.extraction.validator import ValidationResult, validate_extraction
from quantforge.strategy.models.enums import (
    CompletenessClass,
    ComponentCoverage,
    ExtractionMethod,
    Implementability,
    MissingAction,
    MissingImpact,
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

logger = logging.getLogger(__name__)

ALL_PILLARS = [
    "market_regime", "market_eligibility", "asset_selection",
    "entry_signal", "position_sizing", "risk_management",
    "exit", "portfolio", "backtesting",
]


# ---------------------------------------------------------------------------
# Pipeline config
# ---------------------------------------------------------------------------

@dataclass
class ExtractionConfig:
    """Runtime configuration for one extraction run."""
    min_section_chars: int = 200
    max_section_tokens: int = 1500
    fallback_to_full_text: bool = True
    full_text_chunk_size_chars: int = 6000
    full_text_chunk_overlap_chars: int = 200
    routing_max_tokens: int = 1000
    extraction_max_tokens_per_group: int = 6000
    validation_max_tokens: int = 3000
    pillar_groups: dict[str, list[str]] | None = None

    @classmethod
    def from_json(cls, config_path: Path) -> "ExtractionConfig":
        """Load config from strategy.json."""
        if not config_path.exists():
            logger.warning("strategy.json not found — using defaults")
            return cls()
        data = json.loads(config_path.read_text(encoding="utf-8"))
        extraction = data.get("extraction", {})
        budgets = data.get("token_budgets", {})
        groups = data.get("pillar_groups")
        return cls(
            min_section_chars=extraction.get("min_section_chars", 200),
            max_section_tokens=extraction.get("max_section_tokens", 1500),
            fallback_to_full_text=extraction.get("fallback_to_full_text", True),
            full_text_chunk_size_chars=extraction.get("full_text_chunk_size_chars", 6000),
            full_text_chunk_overlap_chars=extraction.get("full_text_chunk_overlap_chars", 200),
            routing_max_tokens=budgets.get("routing_call", 1000),
            extraction_max_tokens_per_group=budgets.get("extraction_per_group", 6000),
            validation_max_tokens=budgets.get("validation_call", 3000),
            pillar_groups=groups,
        )


# ---------------------------------------------------------------------------
# Pipeline result
# ---------------------------------------------------------------------------

@dataclass
class PipelineResult:
    """Full result of one extraction pipeline run."""
    paper_id: UUID
    spec: StrategySpecification | None = None
    success: bool = False
    is_partial: bool = False        # True when budget exhausted mid-paper
    extraction_failed: bool = False  # True when Call 1 or all groups failed
    error: str | None = None
    used_full_text_fallback: bool = False
    routing: RoutingResult | None = None
    extraction: ExtractionResult | None = None
    validation: ValidationResult | None = None


# ---------------------------------------------------------------------------
# Package loading
# ---------------------------------------------------------------------------

def _load_package(package_dir: Path) -> tuple[str, str, list[dict]]:
    """
    Load metadata.json, text.txt, and sections.json from a package directory.

    Returns:
        (abstract, full_text, sections_data)
    """
    abstract = ""
    metadata_path = package_dir / "metadata.json"
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        abstract = metadata.get("abstract", "")

    full_text = ""
    text_path = package_dir / "text.txt"
    if text_path.exists():
        full_text = text_path.read_text(encoding="utf-8")

    sections_data: list[dict] = []
    sections_path = package_dir / "sections.json"
    if sections_path.exists():
        sections_data = json.loads(sections_path.read_text(encoding="utf-8"))

    return abstract, full_text, sections_data


# ---------------------------------------------------------------------------
# Audit assembly
# ---------------------------------------------------------------------------

def _build_audit(
    routing: RoutingResult,
    all_sections: list[Section],
    filtered_indices: list[int],
    extraction: ExtractionResult,
    validation: ValidationResult,
    formula_quality_map: dict[int, str],
) -> ExtractionAudit:
    """Assemble the extraction audit from all stage results."""
    sections_used: set[int] = set()
    for indices in routing.routing_map.values():
        sections_used.update(indices)

    all_indices = {s.index for s in all_sections}
    sections_skipped = sorted(
        all_indices - sections_used - set(filtered_indices)
    )

    return ExtractionAudit(
        routing_map=routing.routing_map,
        sections_used=sorted(sections_used),
        sections_skipped=sections_skipped,
        sections_filtered_too_short=filtered_indices,
        total_input_tokens=(
            routing.input_tokens
            + extraction.total_input_tokens
            + validation.input_tokens
        ),
        total_output_tokens=(
            routing.output_tokens
            + extraction.total_output_tokens
            + validation.output_tokens
        ),
        extraction_date=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Spec assembly
# ---------------------------------------------------------------------------

def _build_pillar_data(raw: dict) -> PillarData:
    """Convert raw LLM pillar dict to a PillarData model."""
    rules = []
    for r in raw.get("rules", []):
        try:
            rules.append(RuleObject(**r))
        except Exception as e:
            logger.warning("Skipping invalid rule object: %s — %s", r, e)

    present_raw = raw.get("present", False)
    if isinstance(present_raw, str) and present_raw == "partial":
        present = "partial"
    else:
        present = bool(present_raw)

    return PillarData(
        present=present,
        source_note=raw.get("source_note"),
        rules=rules,
        missing_note=raw.get("missing_note"),
    )


def _build_coverage_map(pillars: dict) -> ComponentCoverageMap:
    """Build coverage map from extracted pillar data."""
    def _cov(raw: dict) -> ComponentCoverage:
        present = raw.get("present", False)
        if present is True:
            return ComponentCoverage.PRESENT
        if present == "partial":
            return ComponentCoverage.PARTIAL
        return ComponentCoverage.ABSENT

    return ComponentCoverageMap(
        market_regime=_cov(pillars.get("market_regime", {})),
        market_eligibility=_cov(pillars.get("market_eligibility", {})),
        asset_selection=_cov(pillars.get("asset_selection", {})),
        entry_signal=_cov(pillars.get("entry_signal", {})),
        position_sizing=_cov(pillars.get("position_sizing", {})),
        risk_management=_cov(pillars.get("risk_management", {})),
        exit=_cov(pillars.get("exit", {})),
        portfolio=_cov(pillars.get("portfolio", {})),
        backtesting=_cov(pillars.get("backtesting", {})),
    )


def _build_missing_components(
    pillars: dict,
    not_found: list[str],
) -> list[MissingComponent]:
    """Build missing component list from absent/partial pillars."""
    missing = []
    for pillar in ALL_PILLARS:
        raw = pillars.get(pillar, {})
        present = raw.get("present", False)

        if present is False or pillar in not_found:
            missing.append(MissingComponent(
                pillar=pillar,
                status="absent",
                impact=MissingImpact.MODERATE,
                description=raw.get("missing_note") or f"{pillar} not found in paper.",
                implication=f"Cannot implement {pillar} component without additional research.",
                recommended_action=MissingAction.REQUIRES_HUMAN_DECISION,
            ))
        elif present == "partial":
            missing.append(MissingComponent(
                pillar=pillar,
                status="partial",
                impact=MissingImpact.MINOR,
                description=raw.get("missing_note") or f"{pillar} partially described.",
                implication=f"{pillar} requires clarification before implementation.",
                recommended_action=MissingAction.REQUIRES_HUMAN_DECISION,
            ))
    return missing


def _build_methodology_assessment(
    validation: ValidationResult,
) -> MethodologyAssessment | None:
    """Build MethodologyAssessment from validation result."""
    if not validation.methodology_assessment:
        return None
    ma = validation.methodology_assessment
    try:
        return MethodologyAssessment(
            implementability=Implementability(
                ma.get("implementability", "research_only")
            ),
            suitable_for_backtest=ma.get("suitable_for_backtest", False),
            primary_strength=ma.get("primary_strength", ""),
            primary_weakness=ma.get("primary_weakness", ""),
            key_missing_components=ma.get("key_missing_components", []),
            reason=ma.get("reason", ""),
        )
    except Exception as e:
        logger.warning("Could not build MethodologyAssessment: %s", e)
        return None


def _build_spec(
    paper_id: UUID,
    all_pillars: dict,
    not_found: list[str],
    validation: ValidationResult,
    audit: ExtractionAudit,
    is_partial: bool,
) -> StrategySpecification:
    """Assemble the full StrategySpecification from pipeline outputs."""
    pillars_obj = StrategyPillars(
        market_regime=_build_pillar_data(all_pillars.get("market_regime", {})),
        market_eligibility=_build_pillar_data(all_pillars.get("market_eligibility", {})),
        asset_selection=_build_pillar_data(all_pillars.get("asset_selection", {})),
        entry_signal=_build_pillar_data(all_pillars.get("entry_signal", {})),
        position_sizing=_build_pillar_data(all_pillars.get("position_sizing", {})),
        risk_management=_build_pillar_data(all_pillars.get("risk_management", {})),
        exit=_build_pillar_data(all_pillars.get("exit", {})),
        portfolio=_build_pillar_data(all_pillars.get("portfolio", {})),
        backtesting=_build_pillar_data(all_pillars.get("backtesting", {})),
    )

    try:
        completeness = CompletenessClass(validation.completeness_class)
    except ValueError:
        completeness = CompletenessClass.RESEARCH_ONLY

    notes = None
    if is_partial:
        notes = "PARTIAL EXTRACTION — budget exhausted before all groups completed."
    if validation.leakage_findings:
        leakage_note = "; ".join(
            f"{lf.rule_id} in {lf.found_in_pillar} → {lf.should_be_in_pillar}"
            for lf in validation.leakage_findings
        )
        notes = f"{notes or ''} Leakage: {leakage_note}".strip()

    return StrategySpecification(
        paper_id=paper_id,
        extracted_by=ExtractionMethod.LLM_ASSISTED,
        completeness_class=completeness,
        component_coverage=_build_coverage_map(all_pillars),
        pillars=pillars_obj,
        missing_components=_build_missing_components(all_pillars, not_found),
        methodology_assessment=_build_methodology_assessment(validation),
        extraction_audit=audit,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Main pipeline entry point
# ---------------------------------------------------------------------------

async def run_extraction_pipeline(
    paper_id: UUID,
    package_dir: Path,
    budget_manager: BudgetManager,
    config: ExtractionConfig | None = None,
) -> PipelineResult:
    """
    Run the full three-call extraction pipeline for one paper.

    Args:
        paper_id: UUID of the paper being extracted.
        package_dir: Path to the Research Document Package directory.
        budget_manager: LLM budget manager — all calls go through this.
        config: Extraction configuration. Loads from strategy.json if None.

    Returns:
        PipelineResult with spec (or partial spec) and audit trail.
    """
    if config is None:
        config_path = Path("config/strategy.json")
        config = ExtractionConfig.from_json(config_path)

    result = PipelineResult(paper_id=paper_id)
    paper_id_str = str(paper_id)

    # --- Load package ---
    abstract, full_text, sections_data = _load_package(package_dir)

    # --- Load and detect sections ---
    raw_sections = load_sections_from_package(sections_data)
    logger.info("Paper %s: loaded %d sections", paper_id_str, len(raw_sections))

    # Build formula quality map for audit
    formula_quality_map = {s.index: s.formula_quality for s in raw_sections}

    # --- Section quality filter ---
    filtered_sections, filtered_indices = filter_sections(
        raw_sections, min_chars=config.min_section_chars
    )

    # --- Full-text fallback ---
    used_fallback = False
    if not filtered_sections:
        if config.fallback_to_full_text and full_text:
            logger.warning(
                "Paper %s: all sections filtered or missing — "
                "falling back to text.txt chunking",
                paper_id_str,
            )
            filtered_sections = chunk_full_text(
                text=full_text,
                chunk_size_chars=config.full_text_chunk_size_chars,
                overlap_chars=config.full_text_chunk_overlap_chars,
            )
            used_fallback = True
            result.used_full_text_fallback = True
        else:
            logger.error(
                "Paper %s: no sections and fallback disabled — extraction failed",
                paper_id_str,
            )
            result.extraction_failed = True
            result.error = "No sections available and full-text fallback disabled."
            return result

    # --- Section size guard ---
    sections = apply_size_guard(
        filtered_sections,
        max_tokens=config.max_section_tokens,
    )
    sections_by_index = {s.index: s for s in sections}

    # --- Call 1: Section Router ---
    logger.info("Paper %s: running Call 1 — Section Router", paper_id_str)
    routing = await route_sections(
        sections=sections,
        abstract=abstract,
        budget_manager=budget_manager,
        max_tokens=config.routing_max_tokens,
        paper_id=paper_id_str,
    )
    result.routing = routing

    if not routing.success and not routing.routing_map:
        logger.error(
            "Paper %s: routing failed completely — marking extraction_failed",
            paper_id_str,
        )
        result.extraction_failed = True
        result.error = routing.error
        return result

    # --- Call 2: Pillar Extractor ---
    logger.info("Paper %s: running Call 2 — Pillar Extractor", paper_id_str)
    try:
        extraction = await extract_all_groups(
            routing_map=routing.routing_map,
            not_found_pillars=routing.not_found,
            sections_by_index=sections_by_index,
            budget_manager=budget_manager,
            pillar_groups=config.pillar_groups,
            max_tokens_per_group=config.extraction_max_tokens_per_group,
            paper_id=paper_id_str,
        )
    except BudgetExhaustedError as e:
        logger.warning(
            "Paper %s: budget exhausted during extraction — saving partial spec",
            paper_id_str,
        )
        result.is_partial = True
        result.error = str(e)
        # Build a minimal validation result for the partial spec
        extraction = ExtractionResult()
        validation = ValidationResult(
            success=False,
            skipped_budget=True,
            methodology_assessment={
                "implementability": "research_only",
                "suitable_for_backtest": False,
                "primary_strength": "Not assessed — budget exhausted.",
                "primary_weakness": "Not assessed — budget exhausted.",
                "key_missing_components": ALL_PILLARS,
                "reason": "Extraction stopped early due to budget exhaustion.",
            },
        )
        audit = _build_audit(
            routing, sections, filtered_indices,
            extraction, validation, formula_quality_map,
        )
        result.spec = _build_spec(
            paper_id, extraction.all_pillars,
            routing.not_found, validation, audit,
            is_partial=True,
        )
        result.extraction = extraction
        result.validation = validation
        return result

    result.extraction = extraction

    if extraction.budget_exhausted_mid_paper:
        result.is_partial = True
        logger.warning(
            "Paper %s: partial extraction — completed groups: %s, skipped: %s",
            paper_id_str,
            extraction.completed_groups,
            extraction.skipped_groups,
        )

    # --- Call 3: Validation ---
    logger.info("Paper %s: running Call 3 — Validation", paper_id_str)
    missing_dicts = [
        {
            "pillar": p,
            "status": "absent",
            "note": extraction.all_pillars.get(p, {}).get("missing_note", ""),
        }
        for p in routing.not_found
    ]

    validation = await validate_extraction(
        extracted_pillars=extraction.all_pillars,
        missing_components=missing_dicts,
        not_found_pillars=routing.not_found,
        budget_manager=budget_manager,
        max_tokens=config.validation_max_tokens,
        paper_id=paper_id_str,
    )
    result.validation = validation

    # --- Assemble audit ---
    audit = _build_audit(
        routing, sections, filtered_indices,
        extraction, validation, formula_quality_map,
    )

    # --- Assemble spec ---
    result.spec = _build_spec(
        paper_id=paper_id,
        all_pillars=extraction.all_pillars,
        not_found=routing.not_found,
        validation=validation,
        audit=audit,
        is_partial=result.is_partial,
    )
    result.success = True

    logger.info(
        "Paper %s: extraction complete — class=%r partial=%s fallback=%s "
        "tokens_in=%d tokens_out=%d",
        paper_id_str,
        result.spec.completeness_class,
        result.is_partial,
        result.used_full_text_fallback,
        audit.total_input_tokens,
        audit.total_output_tokens,
    )
    return result
