"""Scaleway Generative APIs adapter implementing `LlmClient` (ADR 0010).

Scaleway speaks the OpenAI chat-completions protocol, so this wraps the
OpenAI SDK. It serves the crisis classifier's primary model; its errors are
meant to reach a fallback quickly, hence one retry and a short timeout.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import openai

from .base import LlmUnavailableError, StreamChunk

# A crisis check answers in about half a second. A slow or failing Scaleway
# must leave the backend's 90 s AI call timeout room for the fallback.
_TIMEOUT = 10.0
_MAX_RETRIES = 1


class ScalewayLlmClient:
    """Production `LlmClient` backed by Scaleway Generative APIs."""

    def __init__(
        self,
        api_key: str,
        project_id: str = "",
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("SCALEWAY_API_KEY must be set")
        # A key restricted to a project other than the organization's default
        # one must name it in the URL (same rule as core/agent.py).
        project = f"/{project_id}" if project_id else ""
        self._client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=f"https://api.scaleway.ai{project}/v1",
            timeout=_TIMEOUT,
            max_retries=_MAX_RETRIES,
            http_client=http_client,
        )

    async def _create(self, kwargs: dict[str, Any]) -> Any:
        try:
            return await self._client.chat.completions.create(**kwargs)
        except (
            openai.RateLimitError,
            openai.InternalServerError,
            openai.APIConnectionError,
        ) as exc:
            raise LlmUnavailableError(f"Scaleway unavailable: {exc}") from exc

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.5,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
    ) -> tuple[str, dict[str, int]]:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if response_format is not None:
            kwargs["response_format"] = response_format
        if reasoning_effort is not None:
            kwargs["reasoning_effort"] = reasoning_effort

        response = await self._create(kwargs)
        content = response.choices[0].message.content or ""
        usage = {
            "input_tokens": response.usage.prompt_tokens,
            "output_tokens": response.usage.completion_tokens,
        }
        return content, usage

    async def chat_stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.5,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
    ) -> AsyncIterator[StreamChunk]:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if reasoning_effort is not None:
            kwargs["reasoning_effort"] = reasoning_effort

        stream = await self._create(kwargs)
        async for chunk in stream:
            choice = chunk.choices[0] if chunk.choices else None
            out: StreamChunk = {"text": (choice.delta.content or "") if choice else ""}
            if choice is not None and choice.finish_reason:
                out["finish_reason"] = choice.finish_reason
            if chunk.usage is not None:
                out["input_tokens"] = chunk.usage.prompt_tokens
                out["output_tokens"] = chunk.usage.completion_tokens
            yield out
