# src/quantforge/llm/providers/openrouter.py

import logging
from typing import Any

import httpx

from quantforge.config.settings import settings
from quantforge.llm.provider import LLMProvider, LLMRequest, LLMResponse, LLMProviderError

logger = logging.getLogger(__name__)

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterProvider(LLMProvider):
    """
    OpenRouter implementation of LLMProvider.
    Supports any model available on OpenRouter including
    Claude, GPT-4, Llama, Mistral and others.
    Swap to a different provider by implementing LLMProvider elsewhere.
    """

    def __init__(
        self,
        model: str,
        max_retries: int = 2,
        timeout: float = 60.0,
    ) -> None:
        self._model = model
        self._max_retries = max_retries
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    @property
    def model(self) -> str:
        return self._model

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                headers={
                    "Authorization": f"Bearer {settings.openrouter_api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/quantforge",
                    "X-Title": "QuantForge Research Pipeline",
                },
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def complete(self, request: LLMRequest) -> LLMResponse:
        messages = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})

        payload = {
            "model": self._model,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }

        last_error: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            try:
                client = self._get_client()
                response = await client.post(OPENROUTER_API_URL, json=payload)
                response.raise_for_status()
                return self._parse_response(response.json())

            except httpx.TimeoutException as e:
                last_error = e
                logger.warning(
                    "OpenRouter timeout on attempt %d/%d",
                    attempt, self._max_retries,
                )

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    last_error = e
                    logger.warning(
                        "OpenRouter rate limited on attempt %d/%d",
                        attempt, self._max_retries,
                    )
                else:
                    raise LLMProviderError(
                        f"HTTP {e.response.status_code} from OpenRouter: {e.response.text}"
                    ) from e

            except Exception as e:
                raise LLMProviderError(
                    f"Unexpected OpenRouter error: {e}"
                ) from e

        raise LLMProviderError(
            f"OpenRouter failed after {self._max_retries} attempts: {last_error}"
        )

    def _parse_response(self, data: dict[str, Any]) -> LLMResponse:
        try:
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            return LLMResponse(
                content=content,
                model=data.get("model", self._model),
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
            )
        except (KeyError, IndexError) as e:
            raise LLMProviderError(
                f"Failed to parse OpenRouter response: {data}"
            ) from e