"""Any OpenAI-compatible chat-completions endpoint as an `LlmClient`.

It serves the crisis classifier (ADR 0005). The backend blocks the chat turn
while the check runs, so a failing endpoint must surface quickly: one retry
and the caller's timeout (LLM_SAFETY_TIMEOUT_SECONDS in production). The
request is shaped and sent by `chat_request` like every other chat call: a
reasoning model gets no temperature, a parameter the endpoint rejects as
unsupported is adjusted and the request sent again, a reasoning model's
answer left empty by the token limit is logged, and content that comes as a
list of chunks (Mistral's reasoning models) is read as its text.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import openai

from .base import LlmUnavailableError, StreamChunk
from .chat_request import answer_text, build_chat_request, create_chat_completion


class OpenAICompatibleLlmClient:
    """Production `LlmClient` for the endpoint at LLM_BASE_URL."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float,
        max_retries: int = 1,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not base_url or not api_key:
            raise ValueError("LLM_BASE_URL and LLM_API_KEY must be set")
        self._client = openai.AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            max_retries=max_retries,
            http_client=http_client,
        )

    async def _create(self, kwargs: dict[str, Any]) -> Any:
        try:
            return await create_chat_completion(self._client, kwargs)
        except (
            openai.RateLimitError,
            openai.InternalServerError,
            # Includes openai.APITimeoutError.
            openai.APIConnectionError,
        ) as exc:
            raise LlmUnavailableError(f"LLM endpoint unavailable: {exc}") from exc

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
        kwargs = build_chat_request(
            model,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            reasoning_effort=reasoning_effort,
        )
        response = await self._create(kwargs)
        # Some endpoints leave out the usage block or the content.
        content = answer_text(response.choices[0].message.content)
        usage = response.usage
        return content, {
            "input_tokens": (usage.prompt_tokens or 0) if usage else 0,
            "output_tokens": (usage.completion_tokens or 0) if usage else 0,
        }

    async def chat_stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.5,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
    ) -> AsyncIterator[StreamChunk]:
        kwargs = build_chat_request(
            model,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
        )
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}
        stream = await self._create(kwargs)
        async for chunk in stream:
            choice = chunk.choices[0] if chunk.choices else None
            delta = choice.delta if choice is not None else None
            out: StreamChunk = {"text": answer_text(delta.content) if delta is not None else ""}
            if choice is not None and choice.finish_reason:
                out["finish_reason"] = choice.finish_reason
            if chunk.usage is not None:
                out["input_tokens"] = chunk.usage.prompt_tokens or 0
                out["output_tokens"] = chunk.usage.completion_tokens or 0
            yield out
