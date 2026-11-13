# tests/llm/test_provider.py

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from quantforge.llm.provider import LLMRequest, LLMResponse, LLMProviderError
from quantforge.llm.providers.openrouter import OpenRouterProvider


SAMPLE_OPENROUTER_RESPONSE = {
    "id": "gen-123",
    "model": "anthropic/claude-3-haiku",
    "choices": [
        {
            "message": {
                "role": "assistant",
                "content": '{"score": 0.8, "rationale": "Novel approach", "evidence": ["Uses daily data"]}',
            }
        }
    ],
    "usage": {
        "prompt_tokens": 150,
        "completion_tokens": 50,
        "total_tokens": 200,
    },
}


def make_provider(**kwargs) -> OpenRouterProvider:
    defaults = {"model": "anthropic/claude-3-haiku", "max_retries": 1}
    defaults.update(kwargs)
    return OpenRouterProvider(**defaults)


def make_mock_response(data: dict, status_code: int = 200):
    mock = MagicMock()
    mock.json = MagicMock(return_value=data)
    mock.status_code = status_code
    mock.raise_for_status = MagicMock()
    return mock


# --- Basic Completion ---

@pytest.mark.asyncio
async def test_complete_returns_response():
    provider = make_provider()
    mock_response = make_mock_response(SAMPLE_OPENROUTER_RESPONSE)

    with patch.object(provider, "_get_client") as mock_client:
        mock_client.return_value.post = AsyncMock(return_value=mock_response)
        response = await provider.complete(LLMRequest(prompt="Evaluate this paper"))

    assert isinstance(response, LLMResponse)
    assert response.model == "anthropic/claude-3-haiku"
    assert response.input_tokens == 150
    assert response.output_tokens == 50
    assert response.total_tokens == 200
    assert "score" in response.content


@pytest.mark.asyncio
async def test_complete_with_system_prompt():
    provider = make_provider()
    mock_response = make_mock_response(SAMPLE_OPENROUTER_RESPONSE)

    with patch.object(provider, "_get_client") as mock_client:
        post_mock = AsyncMock(return_value=mock_response)
        mock_client.return_value.post = post_mock
        await provider.complete(LLMRequest(
            prompt="Evaluate this",
            system_prompt="You are an evaluator",
        ))

    call_kwargs = post_mock.call_args
    payload = call_kwargs[1]["json"]
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][1]["role"] == "user"


# --- Cost Estimation ---

def test_estimated_cost_usd():
    response = LLMResponse(
        content="test",
        model="anthropic/claude-3-haiku",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        total_tokens=2_000_000,
    )
    # Input: $0.25, Output: $1.25 → total $1.50
    assert response.estimated_cost_usd == 1.5


def test_estimated_cost_zero_tokens():
    response = LLMResponse(
        content="",
        model="anthropic/claude-3-haiku",
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
    )
    assert response.estimated_cost_usd == 0.0


# --- Error Handling ---

@pytest.mark.asyncio
async def test_timeout_raises_provider_error_after_retries():
    import httpx
    provider = make_provider(max_retries=2)

    with patch.object(provider, "_get_client") as mock_client:
        mock_client.return_value.post = AsyncMock(
            side_effect=httpx.TimeoutException("timed out")
        )
        with pytest.raises(LLMProviderError, match="failed after"):
            await provider.complete(LLMRequest(prompt="test"))


@pytest.mark.asyncio
async def test_http_4xx_raises_immediately():
    import httpx
    provider = make_provider(max_retries=3)

    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.text = "Unauthorized"

    with patch.object(provider, "_get_client") as mock_client:
        mock_client.return_value.post = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "Unauthorized", request=MagicMock(), response=mock_resp
            )
        )
        with pytest.raises(LLMProviderError, match="401"):
            await provider.complete(LLMRequest(prompt="test"))


@pytest.mark.asyncio
async def test_malformed_response_raises_provider_error():
    provider = make_provider()
    bad_response = make_mock_response({"bad": "response"})

    with patch.object(provider, "_get_client") as mock_client:
        mock_client.return_value.post = AsyncMock(return_value=bad_response)
        with pytest.raises(LLMProviderError, match="parse"):
            await provider.complete(LLMRequest(prompt="test"))


# --- Context Manager ---

@pytest.mark.asyncio
async def test_provider_context_manager():
    async with OpenRouterProvider(model="anthropic/claude-3-haiku") as provider:
        assert provider.model == "anthropic/claude-3-haiku"