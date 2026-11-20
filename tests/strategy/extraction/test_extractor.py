# tests/strategy/extraction/test_extractor.py

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from quantforge.llm.budget import BudgetManager, BudgetExhaustedError
from quantforge.llm.provider import LLMResponse, LLMProviderError
from quantforge.strategy.extraction.chunker import Section, FormulaQuality
from quantforge.strategy.extraction.extractor import (
    extract_all_groups,
    extract_pillar_group,
    _empty_pillar,
    DEFAULT_PILLAR_GROUPS,
)


SAMPLE_ENTRY_EXIT_RESPONSE = {
    "market_regime": {
        "present": True,
        "source_note": "Section 2",
        "rules": [
            {
                "rule_id": "regime_1",
                "description": "Market is bullish when SPX > 200-day MA.",
                "provenance": "explicit",
                "source_ref": "Section 2, p.4",
            }
        ],
    },
    "market_eligibility": {
        "present": False,
        "source_note": None,
        "rules": [],
        "missing_note": "Not described.",
    },
    "entry_signal": {
        "present": True,
        "source_note": "Section 3",
        "rules": [
            {
                "rule_id": "entry_1",
                "description": "Buy top decile by 12-1 month momentum.",
                "provenance": "explicit",
                "source_ref": "Section 3, p.6",
                "formula": "R(t-12,t-1) = P_{t-1}/P_{t-12} - 1",
                "timeframe": "monthly",
                "data_inputs": "monthly closing price",
            }
        ],
    },
    "exit": {
        "present": "partial",
        "source_note": "Section 3",
        "rules": [
            {
                "rule_id": "exit_1",
                "description": "Rebalance monthly.",
                "provenance": "explicit",
                "source_ref": "Section 3, p.7",
            }
        ],
        "missing_note": "No stop-loss specified.",
    },
}


def make_sections_by_index(indices: list[int]) -> dict[int, Section]:
    return {
        i: Section(
            index=i,
            heading=f"Section {i}",
            content=f"Content for section {i}. " * 20,
            formula_quality=FormulaQuality.CLEAN,
        )
        for i in indices
    }


def make_llm_response(data: dict) -> LLMResponse:
    return LLMResponse(
        content=json.dumps(data),
        model="anthropic/claude-3-haiku",
        input_tokens=500,
        output_tokens=400,
        total_tokens=900,
    )


def make_budget(response: LLMResponse | None = None) -> BudgetManager:
    provider = MagicMock()
    provider.model = "anthropic/claude-3-haiku"
    provider.complete = AsyncMock(
        return_value=response or make_llm_response(SAMPLE_ENTRY_EXIT_RESPONSE)
    )
    return BudgetManager(
        provider=provider,
        max_spend_usd=5.0,
        warn_at_usd=3.0,
        enabled=True,
    )


# --- Single group extraction ---

@pytest.mark.asyncio
async def test_extract_group_returns_all_pillars():
    routing_map = {
        "market_regime": [2],
        "market_eligibility": [2],
        "entry_signal": [3],
        "exit": [3],
    }
    sections = make_sections_by_index([2, 3])
    budget = make_budget()

    result = await extract_pillar_group(
        group_name="entry_exit_regime",
        group_pillars=["market_regime", "market_eligibility", "entry_signal", "exit"],
        routing_map=routing_map,
        sections_by_index=sections,
        budget_manager=budget,
    )

    assert result.success
    assert "entry_signal" in result.extracted
    assert "exit" in result.extracted


@pytest.mark.asyncio
async def test_extract_group_no_sections_returns_empty_pillars():
    """When router found no sections for a group, all pillars are empty."""
    routing_map = {}  # nothing mapped
    sections = make_sections_by_index([])
    budget = make_budget()

    result = await extract_pillar_group(
        group_name="entry_exit_regime",
        group_pillars=["entry_signal", "exit"],
        routing_map=routing_map,
        sections_by_index=sections,
        budget_manager=budget,
    )

    assert result.success
    assert result.extracted["entry_signal"]["present"] is False
    # LLM was never called
    budget._provider.complete.assert_not_called()


@pytest.mark.asyncio
async def test_extract_group_fills_missing_pillars():
    """Pillars absent from LLM output are filled with empty structure."""
    incomplete_response = {
        "entry_signal": SAMPLE_ENTRY_EXIT_RESPONSE["entry_signal"],
        # exit is missing from response
    }
    budget = make_budget(make_llm_response(incomplete_response))
    routing_map = {"entry_signal": [3], "exit": [3]}
    sections = make_sections_by_index([3])

    result = await extract_pillar_group(
        group_name="entry_exit",
        group_pillars=["entry_signal", "exit"],
        routing_map=routing_map,
        sections_by_index=sections,
        budget_manager=budget,
    )

    assert result.success
    assert "exit" in result.extracted
    assert result.extracted["exit"]["present"] is False


@pytest.mark.asyncio
async def test_extract_group_retries_on_malformed_json():
    provider = MagicMock()
    provider.model = "anthropic/claude-3-haiku"
    bad = LLMResponse(
        content="not json {{",
        model="anthropic/claude-3-haiku",
        input_tokens=100, output_tokens=10, total_tokens=110,
    )
    good = make_llm_response({"entry_signal": SAMPLE_ENTRY_EXIT_RESPONSE["entry_signal"]})
    provider.complete = AsyncMock(side_effect=[bad, good])

    budget = BudgetManager(
        provider=provider, max_spend_usd=5.0, warn_at_usd=3.0, enabled=True,
    )
    routing_map = {"entry_signal": [3]}
    sections = make_sections_by_index([3])

    result = await extract_pillar_group(
        group_name="test",
        group_pillars=["entry_signal"],
        routing_map=routing_map,
        sections_by_index=sections,
        budget_manager=budget,
    )

    assert result.success


# --- Full extraction ---

@pytest.mark.asyncio
async def test_extract_all_groups_merges_all_pillars():
    """extract_all_groups returns a dict covering all 9 pillars."""
    routing_map = {p: [1] for p in [
        "market_regime", "market_eligibility", "asset_selection",
        "entry_signal", "position_sizing", "risk_management",
        "exit", "portfolio", "backtesting",
    ]}
    sections = make_sections_by_index([1])

    # Build a valid response for each group
    full_response = {p: _empty_pillar(p) for p in DEFAULT_PILLAR_GROUPS["entry_exit_regime"]}
    provider = MagicMock()
    provider.model = "anthropic/claude-3-haiku"
    provider.complete = AsyncMock(return_value=make_llm_response(full_response))

    budget = BudgetManager(
        provider=provider, max_spend_usd=5.0, warn_at_usd=3.0, enabled=True,
    )

    result = await extract_all_groups(
        routing_map=routing_map,
        not_found_pillars=[],
        sections_by_index=sections,
        budget_manager=budget,
    )

    assert len(result.all_pillars) == 9


@pytest.mark.asyncio
async def test_extract_all_groups_stops_on_budget_exhaustion():
    """Budget exhaustion mid-paper is recorded and remaining groups skipped."""
    routing_map = {p: [1] for p in [
        "market_regime", "entry_signal", "exit",
        "position_sizing", "risk_management",
    ]}
    sections = make_sections_by_index([1])

    call_count = 0

    async def mock_call(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise BudgetExhaustedError("exhausted")
        return make_llm_response(
            {p: _empty_pillar(p) for p in DEFAULT_PILLAR_GROUPS["entry_exit_regime"]}
        )

    provider = MagicMock()
    provider.model = "anthropic/claude-3-haiku"
    provider.complete = AsyncMock(side_effect=mock_call)

    budget = BudgetManager(
        provider=provider, max_spend_usd=5.0, warn_at_usd=3.0, enabled=True,
    )
    budget.call = mock_call

    result = await extract_all_groups(
        routing_map=routing_map,
        not_found_pillars=[],
        sections_by_index=sections,
        budget_manager=budget,
    )

    assert result.budget_exhausted_mid_paper
    assert len(result.skipped_groups) > 0


@pytest.mark.asyncio
async def test_not_found_pillars_pre_filled_as_absent():
    """Pillars in not_found list are marked absent without an LLM call."""
    routing_map = {}
    sections = make_sections_by_index([])
    budget = make_budget()

    result = await extract_all_groups(
        routing_map=routing_map,
        not_found_pillars=["entry_signal", "exit"],
        sections_by_index=sections,
        budget_manager=budget,
    )

    assert result.all_pillars["entry_signal"]["present"] is False
    assert result.all_pillars["exit"]["present"] is False