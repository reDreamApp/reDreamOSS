"""Mistral SDK adapter implementing `LlmClient` (ADR 0010 — pinned models).

This file is the only place that imports the Mistral SDK. Routes depend on
the `LlmClient` protocol.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from mistralai import Mistral
from mistralai.models import SDKError

from .base import LlmUnavailableError, StreamChunk

log = logging.getLogger(__name__)

# Backoff schedule for transient provider errors (429 / 5xx). A chat turn
# makes up to 3 LLM calls (safety + route + respond), so rate-limit bursts
# mid-conversation are expected, not exceptional. The schedule must outlast a
# per-minute quota window but stay inside the backend's 90 s AI call timeout.
_RETRY_DELAYS = (1.0, 2.0, 5.0, 10.0, 20.0)
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class MistralLlmClient:
    """Production `LlmClient` backed by the Mistral API."""

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("MISTRAL_API_KEY must be set")
        self._client = Mistral(api_key=api_key)

    async def _complete_with_retry(self, kwargs: dict[str, Any]) -> Any:
        for attempt, delay in enumerate((*_RETRY_DELAYS, None)):
            try:
                return await self._client.chat.complete_async(**kwargs)
            except SDKError as exc:
                if exc.status_code not in _RETRYABLE_STATUS:
                    raise
                if delay is None:
                    raise LlmUnavailableError(
                        f"Mistral unavailable after {attempt + 1} attempts "
                        f"(status {exc.status_code})"
                    ) from exc
                log.warning(
                    "Mistral returned %s (attempt %d); retrying in %.0fs",
                    exc.status_code,
                    attempt + 1,
                    delay,
                )
                await asyncio.sleep(delay)
        raise AssertionError("unreachable")

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

        response = await self._complete_with_retry(kwargs)
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
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if reasoning_effort is not None:
            kwargs["reasoning_effort"] = reasoning_effort

        stream = await self._client.chat.stream_async(**kwargs)
        async for chunk in stream:
            data = chunk.data
            choice = data.choices[0] if data.choices else None
            text = (choice.delta.content or "") if choice and choice.delta else ""
            finish_reason = choice.finish_reason if choice else None
            usage = getattr(data, "usage", None)

            out: StreamChunk = {"text": text}
            if finish_reason:
                out["finish_reason"] = finish_reason
            if usage is not None:
                out["input_tokens"] = usage.prompt_tokens
                out["output_tokens"] = usage.completion_tokens
            yield out
