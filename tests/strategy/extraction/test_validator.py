# tests/strategy/extraction/test_validator.py

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from quantforge.llm.budget import BudgetManager, BudgetExhaustedError
from quantforge.llm.provider import LLMResponse, LLMProviderError
from quantforge.strategy.extraction.validator import (
    validate_extraction,
    ValidationResult,
    FlaggedRule,
    LeakageFinding,
)


VALID_VALIDATION_RESPONSE = {
    "completeness_class": "entry_focused",
    "methodology_assessment": {
        "implementability": "requires_additions",
        "suitable_for_backtest": True,
        "primary_strength": "Clear entry signal with explicit formula.",
        "primary_weakness": "No stop-loss or position sizing rules.",
        "key_missing_components": ["position_sizing", "risk_management"],
        "reason": (
            "The paper provides a complete entry signal but omits exit "
            "and risk rules required for a live implementation."
        ),
    },
    "flagged_rules": [
        {
            "pillar": "entry_signal",
            "rule_id": "entry_1",
            "issue": "source_ref is vague — 'Section 3' without page number.",
        }
    ],
    "leakage_findings": [
        {
            "found_in_pillar": "entry_signal",
            "rule_id": "entry_2",
            "should_be_in_pillar": "exit",
            "reason": "Rule describes a stop-loss exit, not an entry condition.",
        }
    ],
}

SAMPLE_PILLARS = {
    "entry_signal": {
        "present": True,
        "rules": [
            {
                "rule_id": "entry_1",
                "description": "Buy top decile by 12-1 month momentum.",
                "provenance": "explicit",
                "source_ref": "Section 3",
            }
        ],
    },
    "exit": {"present": False, "rules": []},
}


def make_llm_response(data: dict) -> LLMResponse:
    return LLMResponse(
        content=json.dumps(data),
        model="anthropic/claude-3-haiku",
        input_tokens=600,
        output_tokens=300,
        total_tokens=900,
    )


def make_budget(response: LLMResponse | None = None) -> BudgetManager:
    provider = MagicMock()
    provider.model = "anthropic/claude-3-haiku"
    provider.complete = AsyncMock(
        return_value=response or make_llm_response(VALID_VALIDATION_RESPONSE)
    )
    return BudgetManager(
        provider=provider,
        max_spend_usd=5.0,
        warn_at_usd=3.0,
        enabled=True,
    )


# --- Happy path ---

@pytest.mark.asyncio
async def test_validation_returns_completeness_class():
    budget = make_budget()
    result = await validate_extraction(
        extracted_pillars=SAMPLE_PILLARS,
        missing_components=[],
        not_found_pillars=["exit"],
        budget_manager=budget,
    )
    assert result.success
    assert result.completeness_class == "entry_focused"


@pytest.mark.asyncio
async def test_validation_returns_methodology_assessment():
    budget = make_budget()
    result = await validate_extraction(
        extracted_pillars=SAMPLE_PILLARS,
        missing_components=[],
        not_found_pillars=[],
        budget_manager=budget,
    )
    assert result.methodology_assessment["implementability"] == "requires_additions"
    assert result.methodology_assessment["suitable_for_backtest"] is True


@pytest.mark.asyncio
async def test_validation_parses_flagged_rules():
    budget = make_budget()
    result = await validate_extraction(
        extracted_pillars=SAMPLE_PILLARS,
        missing_components=[],
        not_found_pillars=[],
        budget_manager=budget,
    )
    assert len(result.flagged_rules) == 1
    assert isinstance(result.flagged_rules[0], FlaggedRule)
    assert result.flagged_rules[0].rule_id == "entry_1"
    assert result.flagged_rules[0].pillar == "entry_signal"


@pytest.mark.asyncio
async def test_validation_parses_leakage_findings():
    budget = make_budget()
    result = await validate_extraction(
        extracted_pillars=SAMPLE_PILLARS,
        missing_components=[],
        not_found_pillars=[],
        budget_manager=budget,
    )
    assert len(result.leakage_findings) == 1
    finding = result.leakage_findings[0]
    assert isinstance(finding, LeakageFinding)
    assert finding.found_in_pillar == "entry_signal"
    assert finding.should_be_in_pillar == "exit"


@pytest.mark.asyncio
async def test_validation_records_token_counts():
    budget = make_budget()
    result = await validate_extraction(
        extracted_pillars=SAMPLE_PILLARS,
        missing_components=[],
        not_found_pillars=[],
        budget_manager=budget,
    )
    assert result.input_tokens == 600
    assert result.output_tokens == 300


# --- No leakage or flags ---

@pytest.mark.asyncio
async def test_validation_clean_response():
    clean_response = {
        "completeness_class": "complete_strategy",
        "methodology_assessment": {
            "implementability": "directly_implementable",
            "suitable_for_backtest": True,
            "primary_strength": "Complete rules.",
            "primary_weakness": "Limited out-of-sample testing.",
            "key_missing_components": [],
            "reason": "All nine pillars present with explicit rules.",
        },
        "flagged_rules": [],
        "leakage_findings": [],
    }
    budget = make_budget(make_llm_response(clean_response))
    result = await validate_extraction(
        extracted_pillars=SAMPLE_PILLARS,
        missing_components=[],
        not_found_pillars=[],
        budget_manager=budget,
    )
    assert result.success
    assert len(result.flagged_rules) == 0
    assert len(result.leakage_findings) == 0


# --- Malformed JSON ---

@pytest.mark.asyncio
async def test_validation_retries_on_malformed_json():
    provider = MagicMock()
    provider.model = "anthropic/claude-3-haiku"
    bad = LLMResponse(
        content="not json {{",
        model="anthropic/claude-3-haiku",
        input_tokens=100, output_tokens=10, total_tokens=110,
    )
    good = make_llm_response(VALID_VALIDATION_RESPONSE)
    provider.complete = AsyncMock(side_effect=[bad, good])

    budget = BudgetManager(
        provider=provider, max_spend_usd=5.0, warn_at_usd=3.0, enabled=True,
    )
    result = await validate_extraction(
        extracted_pillars=SAMPLE_PILLARS,
        missing_components=[],
        not_found_pillars=[],
        budget_manager=budget,
    )
    assert result.success
    assert result.completeness_class == "entry_focused"


@pytest.mark.asyncio
async def test_validation_fails_after_two_bad_responses():
    provider = MagicMock()
    provider.model = "anthropic/claude-3-haiku"
    bad = LLMResponse(
        content="not json",
        model="anthropic/claude-3-haiku",
        input_tokens=100, output_tokens=10, total_tokens=110,
    )
    provider.complete = AsyncMock(return_value=bad)

    budget = BudgetManager(
        provider=provider, max_spend_usd=5.0, warn_at_usd=3.0, enabled=True,
    )
    result = await validate_extraction(
        extracted_pillars=SAMPLE_PILLARS,
        missing_components=[],
        not_found_pillars=[],
        budget_manager=budget,
    )
    assert not result.success
    assert "Malformed JSON" in result.error


# --- Budget exhaustion ---

@pytest.mark.asyncio
async def test_validation_skipped_on_budget_exhaustion():
    budget = BudgetManager(
        provider=MagicMock(),
        max_spend_usd=0.0,
        warn_at_usd=0.0,
        enabled=True,
    )
    result = await validate_extraction(
        extracted_pillars=SAMPLE_PILLARS,
        missing_components=[],
        not_found_pillars=[],
        budget_manager=budget,
    )
    assert not result.success
    assert result.skipped_budget
    assert result.methodology_assessment["implementability"] == "research_only"


# --- Provider error ---

@pytest.mark.asyncio
async def test_validation_provider_error():
    provider = MagicMock()
    provider.model = "anthropic/claude-3-haiku"
    provider.complete = AsyncMock(side_effect=LLMProviderError("provider down"))

    budget = BudgetManager(
        provider=provider, max_spend_usd=5.0, warn_at_usd=3.0, enabled=True,
    )
    result = await validate_extraction(
        extracted_pillars=SAMPLE_PILLARS,
        missing_components=[],
        not_found_pillars=[],
        budget_manager=budget,
    )
    assert not result.success
    assert "provider down" in result.error