# tests/strategy/extraction/test_router.py

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from quantforge.llm.budget import BudgetManager, BudgetExhaustedError
from quantforge.llm.provider import LLMResponse, LLMProviderError
from quantforge.strategy.extraction.chunker import Section, FormulaQuality
from quantforge.strategy.extraction.router import route_sections, RoutingResult


VALID_ROUTING_RESPONSE = {
    "routing_map": {
        "market_regime": [1, 2],
        "market_eligibility": [2],
        "asset_selection": [3],
        "entry_signal": [4, 5],
        "position_sizing": [5],
        "risk_management": [5, 6],
        "exit": [4, 6],
        "portfolio": [7],
        "backtesting": [8],
    },
    "not_found": [],
}


def make_sections(n: int = 9) -> list[Section]:
    return [
        Section(
            index=i,
            heading=f"Section {i}",
            content=f"Content of section {i} with enough text to pass filters. " * 5,
            formula_quality=FormulaQuality.CLEAN,
        )
        for i in range(1, n + 1)
    ]


def make_llm_response(data: dict) -> LLMResponse:
    return LLMResponse(
        content=json.dumps(data),
        model="anthropic/claude-3-haiku",
        input_tokens=300,
        output_tokens=150,
        total_tokens=450,
    )


def make_budget(response: LLMResponse) -> BudgetManager:
    provider = MagicMock()
    provider.model = "anthropic/claude-3-haiku"
    provider.complete = AsyncMock(return_value=response)
    return BudgetManager(
        provider=provider,
        max_spend_usd=5.0,
        warn_at_usd=3.0,
        enabled=True,
    )


# --- Happy path ---

@pytest.mark.asyncio
async def test_router_returns_routing_map():
    sections = make_sections()
    budget = make_budget(make_llm_response(VALID_ROUTING_RESPONSE))

    result = await route_sections(
        sections=sections,
        abstract="We study momentum strategies.",
        budget_manager=budget,
        paper_id="test-paper-1",
    )

    assert result.success
    assert "entry_signal" in result.routing_map
    assert 4 in result.routing_map["entry_signal"]


@pytest.mark.asyncio
async def test_router_records_token_counts():
    sections = make_sections()
    budget = make_budget(make_llm_response(VALID_ROUTING_RESPONSE))

    result = await route_sections(
        sections=sections,
        abstract="We study momentum strategies.",
        budget_manager=budget,
    )

    assert result.input_tokens == 300
    assert result.output_tokens == 150


@pytest.mark.asyncio
async def test_router_filters_nonexistent_indices():
    """Section indices returned by LLM that don't exist are silently removed."""
    response_with_bad_indices = {
        "routing_map": {
            "entry_signal": [4, 999],  # 999 doesn't exist
            "exit": [6],
            "market_regime": [],
            "market_eligibility": [],
            "asset_selection": [],
            "position_sizing": [],
            "risk_management": [],
            "portfolio": [],
            "backtesting": [],
        },
        "not_found": [],
    }
    sections = make_sections()
    budget = make_budget(make_llm_response(response_with_bad_indices))

    result = await route_sections(
        sections=sections,
        abstract="abstract",
        budget_manager=budget,
    )

    assert 999 not in result.routing_map.get("entry_signal", [])
    assert 4 in result.routing_map.get("entry_signal", [])


@pytest.mark.asyncio
async def test_router_moves_empty_pillars_to_not_found():
    """Pillars with no valid indices end up in not_found."""
    response = {
        "routing_map": {
            "entry_signal": [4],
            "market_regime": [],       # empty list
            "market_eligibility": [],
            "asset_selection": [],
            "position_sizing": [],
            "risk_management": [],
            "exit": [],
            "portfolio": [],
            "backtesting": [],
        },
        "not_found": [],
    }
    sections = make_sections()
    budget = make_budget(make_llm_response(response))

    result = await route_sections(
        sections=sections,
        abstract="abstract",
        budget_manager=budget,
    )

    assert "market_regime" in result.not_found


# --- No sections ---

@pytest.mark.asyncio
async def test_router_no_sections_returns_all_not_found():
    budget = make_budget(make_llm_response(VALID_ROUTING_RESPONSE))

    result = await route_sections(
        sections=[],
        abstract="abstract",
        budget_manager=budget,
    )

    assert not result.success
    assert len(result.not_found) == 9  # all pillars


# --- Malformed JSON retry ---

@pytest.mark.asyncio
async def test_router_retries_on_malformed_json():
    """Router retries once on malformed JSON then succeeds."""
    provider = MagicMock()
    provider.model = "anthropic/claude-3-haiku"

    good_response = LLMResponse(
        content=json.dumps(VALID_ROUTING_RESPONSE),
        model="anthropic/claude-3-haiku",
        input_tokens=300,
        output_tokens=150,
        total_tokens=450,
    )
    bad_response = LLMResponse(
        content="this is not json {{{",
        model="anthropic/claude-3-haiku",
        input_tokens=100,
        output_tokens=20,
        total_tokens=120,
    )
    provider.complete = AsyncMock(side_effect=[bad_response, good_response])

    budget = BudgetManager(
        provider=provider,
        max_spend_usd=5.0,
        warn_at_usd=3.0,
        enabled=True,
    )
    sections = make_sections()

    result = await route_sections(
        sections=sections,
        abstract="abstract",
        budget_manager=budget,
    )

    assert result.success
    assert "entry_signal" in result.routing_map


@pytest.mark.asyncio
async def test_router_marks_failed_after_two_bad_responses():
    """After two malformed responses, routing marks all pillars not_found."""
    provider = MagicMock()
    provider.model = "anthropic/claude-3-haiku"
    bad_response = LLMResponse(
        content="not json",
        model="anthropic/claude-3-haiku",
        input_tokens=100,
        output_tokens=10,
        total_tokens=110,
    )
    provider.complete = AsyncMock(return_value=bad_response)

    budget = BudgetManager(
        provider=provider,
        max_spend_usd=5.0,
        warn_at_usd=3.0,
        enabled=True,
    )
    sections = make_sections()

    result = await route_sections(
        sections=sections,
        abstract="abstract",
        budget_manager=budget,
    )

    assert not result.success
    assert len(result.not_found) == 9


# --- Budget exhaustion ---

@pytest.mark.asyncio
async def test_router_budget_exhausted():
    budget = BudgetManager(
        provider=MagicMock(),
        max_spend_usd=0.0,
        warn_at_usd=0.0,
        enabled=True,
    )
    sections = make_sections()

    result = await route_sections(
        sections=sections,
        abstract="abstract",
        budget_manager=budget,
    )

    assert not result.success
    assert len(result.not_found) == 9


# --- Provider error ---

@pytest.mark.asyncio
async def test_router_provider_error():
    provider = MagicMock()
    provider.model = "anthropic/claude-3-haiku"
    provider.complete = AsyncMock(side_effect=LLMProviderError("provider down"))

    budget = BudgetManager(
        provider=provider,
        max_spend_usd=5.0,
        warn_at_usd=3.0,
        enabled=True,
    )
    sections = make_sections()

    result = await route_sections(
        sections=sections,
        abstract="abstract",
        budget_manager=budget,
    )

    assert not result.success
    assert "provider down" in result.error