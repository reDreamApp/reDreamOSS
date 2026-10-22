"""Provider-agnostic LLM client interface.

Routes depend on `LlmClient`, not on a concrete provider. This is the only
boundary at which a new LLM provider would be introduced.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol, TypedDict


class LlmUnavailableError(Exception):
    """The provider is temporarily unavailable (rate limit / 5xx / connection /
    timeout) after retries.

    It names the provider outage in the AIServiceError the backend's
    ai_client raises, so logs distinguish "try again shortly" from a real bug.
    """


class StreamChunk(TypedDict, total=False):
    """One chunk of a streaming chat response."""

    text: str
    """Partial text. Cumulative text is the concatenation of every chunk's `text`."""

    finish_reason: str | None
    """Terminal reason ('stop', 'length', ...) on the final chunk; None mid-stream."""

    input_tokens: int
    """Total prompt tokens — set on the final chunk only."""

    output_tokens: int
    """Total completion tokens — set on the final chunk only."""


class LlmClient(Protocol):
    """Async LLM client protocol.

    Implementations:
        - OpenAICompatibleLlmClient (production, crisis classifier)
        - FakeLlmClient (tests + offline dev)
    """

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
        """Non-streaming chat completion.

        Returns:
            (content, usage) where usage is `{"input_tokens": int, "output_tokens": int}`.
        """
        ...

    def chat_stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.5,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Streaming chat completion.

        Structured output is intentionally NOT supported on the streaming path —
        the caller assembles JSON and parses at end-of-stream (ADR 0009).
        """
        ...
