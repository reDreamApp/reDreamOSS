"""Fake LLM client for tests + offline dev.

Pre-load responses with `enqueue(content, ...)`. They're returned in FIFO order
by `chat()` / `chat_stream()`. Streaming yields the response one word at a time
to match the rough shape of real provider streams.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from typing import Any

from .base import StreamChunk


class FakeLlmClient:
    """Test double for `LlmClient`.

    Example:
        fake = FakeLlmClient()
        fake.enqueue("That sounds difficult.", input_tokens=12, output_tokens=4)
        content, usage = await fake.chat(model="x", messages=[])
        # content == "That sounds difficult."
    """

    def __init__(self) -> None:
        self._queue: deque[tuple[str, dict[str, int]]] = deque()
        self.calls: list[dict[str, Any]] = []

    def enqueue(
        self,
        content: str,
        *,
        input_tokens: int = 10,
        output_tokens: int = 20,
    ) -> None:
        self._queue.append(
            (content, {"input_tokens": input_tokens, "output_tokens": output_tokens})
        )

    def _pop(self) -> tuple[str, dict[str, int]]:
        if not self._queue:
            raise RuntimeError(
                "FakeLlmClient queue is empty — enqueue() a response before calling chat()."
            )
        return self._queue.popleft()

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
        self.calls.append(
            {
                "method": "chat",
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "response_format": response_format,
                "reasoning_effort": reasoning_effort,
            }
        )
        return self._pop()

    async def chat_stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.5,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
    ) -> AsyncIterator[StreamChunk]:
        self.calls.append(
            {
                "method": "chat_stream",
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "reasoning_effort": reasoning_effort,
            }
        )
        content, usage = self._pop()
        words = content.split(" ")
        for i, word in enumerate(words):
            await asyncio.sleep(0)
            text = word if i == 0 else " " + word
            yield StreamChunk(text=text)
        yield StreamChunk(
            text="",
            finish_reason="stop",
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
        )
