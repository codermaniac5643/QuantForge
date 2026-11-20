# tests/strategy/extraction/test_pipeline.py

import json
import uuid
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from quantforge.llm.budget import BudgetManager, BudgetExhaustedError
from quantforge.llm.provider import LLMResponse
from quantforge.strategy.extraction.pipeline import (
    ExtractionConfig,
    PipelineResult,
    run_extraction_pipeline,
)
from quantforge.strategy.extraction.extractor import _empty_pillar
from quantforge.strategy.models.enums import CompletenessClass


# --- Fixtures ---

SAMPLE_SECTIONS = [
    {"heading": "Abstract", "page": 1, "content": "We study cross-sectional momentum strategies " * 20},
    {"heading": "Introduction", "page": 2, "content": "Momentum has been documented extensively " * 20},
    {"heading": "Methodology", "page": 3, "content": "We rank stocks by 12-1 month return " * 20},
    {"heading": "Results", "page": 4, "content": "The strategy generates significant alpha " * 20},
    {"heading": "Conclusion", "page": 5, "content": "We confirm momentum premium is robust " * 20},
]

SAMPLE_METADATA = {
    "title": "Cross-Sectional Momentum",
    "abstract": "We study momentum strategies using daily return data.",
    "authors": [{"name": "Jane Smith"}],
}

ROUTING_RESPONSE = {
    "routing_map": {
        "market_regime": [0],
        "market_eligibility": [1],
        "asset_selection": [2],
        "entry_signal": [2, 3],
        "position_sizing": [2],
        "risk_management": [2],
        "exit": [3],
        "portfolio": [3],
        "backtesting": [4],
    },
    "not_found": [],
}

EXTRACTION_RESPONSE = {
    p: {
        "present": True,
        "source_note": f"Section {i}",
        "rules": [
            {
                "rule_id": f"{p}_1",
                "description": f"Rule for {p}.",
                "provenance": "explicit",
                "source_ref": f"Section {i}, p.{i + 2}",
            }
        ],
    }
    for i, p in enumerate([
        "market_regime", "market_eligibility", "entry_signal", "exit",
    ])
}

VALIDATION_RESPONSE = {
    "completeness_class": "entry_focused",
    "methodology_assessment": {
        "implementability": "requires_additions",
        "suitable_for_backtest": True,
        "primary_strength": "Clear entry signal.",
        "primary_weakness": "Missing stop-loss.",
        "key_missing_components": ["position_sizing"],
        "reason": "Entry rules present but risk rules absent.",
    },
    "flagged_rules": [],
    "leakage_findings": [],
}


def make_package_dir(tmp_path: Path) -> Path:
    pkg = tmp_path / "package"
    pkg.mkdir()
    (pkg / "sections.json").write_text(
        json.dumps(SAMPLE_SECTIONS), encoding="utf-8"
    )
    (pkg / "metadata.json").write_text(
        json.dumps(SAMPLE_METADATA), encoding="utf-8"
    )
    (pkg / "text.txt").write_text(
        "Full paper text. " * 500, encoding="utf-8"
    )
    return pkg


def make_response_sequence() -> list[LLMResponse]:
    """Return responses for routing + 4 extraction groups + validation."""
    responses = [
        LLMResponse(
            content=json.dumps(ROUTING_RESPONSE),
            model="anthropic/claude-3-haiku",
            input_tokens=300, output_tokens=150, total_tokens=450,
        ),
    ]
    # 4 extraction groups
    for _ in range(4):
        responses.append(LLMResponse(
            content=json.dumps(EXTRACTION_RESPONSE),
            model="anthropic/claude-3-haiku",
            input_tokens=500, output_tokens=400, total_tokens=900,
        ))
    # validation
    responses.append(LLMResponse(
        content=json.dumps(VALIDATION_RESPONSE),
        model="anthropic/claude-3-haiku",
        input_tokens=600, output_tokens=300, total_tokens=900,
    ))
    return responses


def make_budget(responses: list[LLMResponse]) -> BudgetManager:
    provider = MagicMock()
    provider.model = "anthropic/claude-3-haiku"
    provider.complete = AsyncMock(side_effect=responses)
    return BudgetManager(
        provider=provider,
        max_spend_usd=10.0,
        warn_at_usd=8.0,
        enabled=True,
        max_calls_per_paper=20,  # enough for routing + 4 groups + validation
    )

def make_config() -> ExtractionConfig:
    return ExtractionConfig(
        min_section_chars=50,  # low threshold so test sections pass
        max_section_tokens=2000,
        routing_max_tokens=1000,
        extraction_max_tokens_per_group=6000,
        validation_max_tokens=3000,
    )


# --- Happy path ---

@pytest.mark.asyncio
async def test_pipeline_full_run_succeeds(tmp_path):
    pkg = make_package_dir(tmp_path)
    budget = make_budget(make_response_sequence())
    config = make_config()

    result = await run_extraction_pipeline(
        paper_id=uuid.uuid4(),
        package_dir=pkg,
        budget_manager=budget,
        config=config,
    )

    assert result.success
    assert result.spec is not None
    assert not result.is_partial
    assert not result.extraction_failed


@pytest.mark.asyncio
async def test_pipeline_spec_has_all_pillars(tmp_path):
    pkg = make_package_dir(tmp_path)
    budget = make_budget(make_response_sequence())
    config = make_config()

    result = await run_extraction_pipeline(
        paper_id=uuid.uuid4(),
        package_dir=pkg,
        budget_manager=budget,
        config=config,
    )

    spec = result.spec
    assert spec.pillars.entry_signal is not None
    assert spec.pillars.exit is not None
    assert spec.pillars.backtesting is not None


@pytest.mark.asyncio
async def test_pipeline_audit_recorded(tmp_path):
    pkg = make_package_dir(tmp_path)
    budget = make_budget(make_response_sequence())
    config = make_config()

    result = await run_extraction_pipeline(
        paper_id=uuid.uuid4(),
        package_dir=pkg,
        budget_manager=budget,
        config=config,
    )

    audit = result.spec.extraction_audit
    assert audit is not None
    assert audit.total_input_tokens > 0
    assert audit.extraction_date is not None
    assert isinstance(audit.routing_map, dict)


@pytest.mark.asyncio
async def test_pipeline_completeness_class_set(tmp_path):
    pkg = make_package_dir(tmp_path)
    budget = make_budget(make_response_sequence())
    config = make_config()

    result = await run_extraction_pipeline(
        paper_id=uuid.uuid4(),
        package_dir=pkg,
        budget_manager=budget,
        config=config,
    )

    assert result.spec.completeness_class == CompletenessClass.ENTRY_FOCUSED


# --- Fallback ---

@pytest.mark.asyncio
async def test_pipeline_uses_fallback_when_no_sections(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "sections.json").write_text("[]", encoding="utf-8")
    (pkg / "metadata.json").write_text(
        json.dumps({"abstract": "momentum study"}), encoding="utf-8"
    )
    (pkg / "text.txt").write_text("Full paper text. " * 1000, encoding="utf-8")

    budget = make_budget(make_response_sequence())
    config = make_config()

    result = await run_extraction_pipeline(
        paper_id=uuid.uuid4(),
        package_dir=pkg,
        budget_manager=budget,
        config=config,
    )

    assert result.used_full_text_fallback


@pytest.mark.asyncio
async def test_pipeline_fails_when_no_sections_and_fallback_disabled(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "sections.json").write_text("[]", encoding="utf-8")
    (pkg / "metadata.json").write_text("{}", encoding="utf-8")
    (pkg / "text.txt").write_text("", encoding="utf-8")

    budget = make_budget([])
    config = ExtractionConfig(
        min_section_chars=50,
        fallback_to_full_text=False,
    )

    result = await run_extraction_pipeline(
        paper_id=uuid.uuid4(),
        package_dir=pkg,
        budget_manager=budget,
        config=config,
    )

    assert result.extraction_failed
    assert not result.success


# --- Partial spec on budget exhaustion ---

@pytest.mark.asyncio
async def test_pipeline_saves_partial_spec_on_budget_exhaustion(tmp_path):
    pkg = make_package_dir(tmp_path)
    config = make_config()

    call_count = 0

    async def mock_call(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return LLMResponse(
                content=json.dumps(ROUTING_RESPONSE),
                model="anthropic/claude-3-haiku",
                input_tokens=300, output_tokens=150, total_tokens=450,
            )
        raise BudgetExhaustedError("exhausted after routing")

    provider = MagicMock()
    provider.model = "anthropic/claude-3-haiku"
    provider.complete = AsyncMock(side_effect=mock_call)
    budget = BudgetManager(
        provider=provider, max_spend_usd=10.0, warn_at_usd=8.0, enabled=True,
    )
    budget.call = mock_call

    result = await run_extraction_pipeline(
        paper_id=uuid.uuid4(),
        package_dir=pkg,
        budget_manager=budget,
        config=config,
    )

    assert result.is_partial
    assert result.spec is not None
    assert "PARTIAL" in (result.spec.notes or "")


# --- Section quality filter ---

@pytest.mark.asyncio
async def test_pipeline_filters_short_sections(tmp_path):
    """Sections below min_section_chars are recorded in audit."""
    short_sections = [
        {"heading": "Abstract", "page": 1, "content": "Short."},
        {"heading": "Introduction", "page": 2,
         "content": "This is a much longer section with enough content. " * 20},
    ]
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "sections.json").write_text(json.dumps(short_sections), encoding="utf-8")
    (pkg / "metadata.json").write_text(
        json.dumps({"abstract": "momentum study"}), encoding="utf-8"
    )
    (pkg / "text.txt").write_text("Full text. " * 500, encoding="utf-8")

    budget = make_budget(make_response_sequence())
    config = make_config()

    result = await run_extraction_pipeline(
        paper_id=uuid.uuid4(),
        package_dir=pkg,
        budget_manager=budget,
        config=config,
    )

    audit = result.spec.extraction_audit
    assert 0 in audit.sections_filtered_too_short  # "Short." is below threshold