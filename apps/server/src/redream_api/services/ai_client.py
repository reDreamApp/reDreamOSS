"""Client for the AI side (redream_ai), called in-process.

Defines a `BaseAiClient` protocol so the chat handler can be tested with a
`FakeAiClient` (see tests/api/conftest.py). The production implementation
calls redream_ai's entry points (`redream_ai.api`) in the same process, with
the dict payloads and return shapes the HTTP boundary had.

Contract reference:
- chat              : full conversation history → next stage + response
- chat_stream       : same request → SSE frames (ADR 0009); the frame
                      contract is documented in redream_ai's api/chat.py
- safety_classify   : user text → risk classification (ADR 0005)
- transcribe        : audio bytes → transcript (ADR 0010)
- rehearsal_generate: dream → audio_url + script + duration

Any failure on the AI side, whatever its type, surfaces as AIServiceError —
as any non-2xx answer did over HTTP. redream_ai is imported on first use, so
importing the backend never builds the IRT agents.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator, AsyncIterator
from typing import TYPE_CHECKING, Any, Protocol

from fastapi import HTTPException
from pydantic import ValidationError

if TYPE_CHECKING:
    from redream_ai.llm.base import LlmClient

log = logging.getLogger(__name__)

# The budgets the HTTP boundary had (AI_REQUEST_TIMEOUT=90). Rehearsal gets
# more: Voxtral has two 60s attempts plus the Storage calls.
_CHAT_TIMEOUT = 90.0
_TRANSCRIBE_TIMEOUT = 90.0
_REHEARSAL_TIMEOUT = 150.0


class AIServiceError(Exception):
    """Raised when the AI side fails, rejects the request or times out."""


class BaseAiClient(Protocol):
    """The chat/audio surface the backend depends on. Tests substitute a fake."""

    async def chat(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def chat_stream(self, payload: dict[str, Any]) -> AsyncGenerator[str, None]: ...
    async def safety_classify(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    async def transcribe(self, *, file_name: str, audio_bytes: bytes) -> dict[str, Any]: ...
    async def rehearsal_generate(self, payload: dict[str, Any]) -> dict[str, Any]: ...


def _reason(op: str, exc: Exception) -> str:
    """What an AI-side failure says in its AIServiceError."""
    from redream_ai.llm.base import LlmUnavailableError

    if isinstance(exc, HTTPException):
        return f"{exc.status_code} {exc.detail}"
    if isinstance(exc, LlmUnavailableError):
        return f"LLM provider temporarily unavailable: {exc}"
    if isinstance(exc, ValidationError):
        # No input values: they can be dream content.
        return f"invalid {exc.title} ({exc.error_count()} validation errors)"
    # Like the old HTTP 500: no internals in the message, the traceback goes
    # to the log.
    log.error("%s: unexpected AI error", op, exc_info=exc)
    return f"internal error ({type(exc).__name__})"


@contextlib.asynccontextmanager
async def _boundary(op: str, seconds: float) -> AsyncIterator[None]:
    """Run one AI call under a deadline; re-raise any failure as AIServiceError.

    Only `Exception`s are mapped: asyncio.CancelledError (client gone) passes
    through untouched.
    """
    try:
        async with asyncio.timeout(seconds) as deadline:
            yield
    except AIServiceError:
        raise
    except Exception as exc:
        if isinstance(exc, TimeoutError) and deadline.expired():
            raise AIServiceError(f"{op} timed out after {seconds:.0f}s") from exc
        raise AIServiceError(f"{op} failed: {_reason(op, exc)}"[:300]) from exc


class InProcessAiClient:
    """Production `BaseAiClient` — calls redream_ai in this process."""

    def __init__(self) -> None:
        from redream_ai.api.safety import build_mistral_llm_client, build_scaleway_llm_client

        # Crisis classifier (ADR 0005): Scaleway primary, direct Mistral
        # fallback, built once per process.
        self._safety_llm: LlmClient | None = build_scaleway_llm_client()
        self._safety_fallback_llm: LlmClient | None = build_mistral_llm_client()

    def log_readiness(self) -> None:
        """Log what the AI side has ready. Importing the IRT core builds its
        agents and the SafetyCritic, so this also does that work up front."""
        from redream_ai.core import irt_app

        if irt_app.routing_agent is None or irt_app.response_agent is None:
            log.warning(
                "IRT agents UNCONFIGURED — set SCALEWAY_API_KEY + MISTRAL_API_KEY; "
                "chat turns fail (502)"
            )
        else:
            log.info(
                "IRT pipeline ready (agents=ok, safety_critic=%s)",
                "ok" if irt_app.safety_critic is not None else "disabled (fail-open)",
            )
        if self._safety_llm is None or self._safety_fallback_llm is None:
            log.warning(
                "crisis classifier UNCONFIGURED — set SCALEWAY_API_KEY + MISTRAL_API_KEY; "
                "chat turns fail (502)"
            )
        else:
            log.info("crisis classifier ready (Scaleway primary, Mistral fallback)")

    async def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        from redream_ai.api import chat as ai_chat
        from redream_ai.models import ChatRequest

        async with _boundary("chat", _CHAT_TIMEOUT):
            resp = await ai_chat.chat(ChatRequest.model_validate(payload))
            # Dumped inside the boundary (here and below): a response that
            # fails to serialize is an AI-side failure too, as the old 500 was.
            return resp.model_dump(mode="json")

    async def chat_stream(self, payload: dict[str, Any]) -> AsyncGenerator[str, None]:
        """Yields one SSE frame per item (``data: <payload>``, stripped, without
        the ``\\n\\n`` separator). A rejected request raises AIServiceError
        before any frame; a failure or 90s of silence mid-stream raises it
        after the frames seen so far."""
        from redream_ai.api import chat as ai_chat
        from redream_ai.models import ChatRequest

        async with _boundary("chat_stream", _CHAT_TIMEOUT):
            stream = ai_chat.chat_stream(ChatRequest.model_validate(payload))
        # aclosing: a consumer that stops early (client gone) closes the AI
        # stream, and through it the provider response, right away.
        async with contextlib.aclosing(stream):
            while True:
                # Per-frame deadline, like the HTTP read timeout it replaces.
                async with _boundary("chat_stream", _CHAT_TIMEOUT):
                    chunk = await anext(stream, None)
                if chunk is None:
                    return
                for frame in chunk.split("\n\n"):
                    if frame.strip():
                        yield frame.strip()

    async def safety_classify(self, payload: dict[str, Any]) -> dict[str, Any]:
        from redream_ai.api import safety as ai_safety
        from redream_ai.models import SafetyRequest

        async with _boundary("safety_classify", _CHAT_TIMEOUT):
            if self._safety_llm is None or self._safety_fallback_llm is None:
                raise AIServiceError(
                    "safety_classify failed: crisis classifier not configured "
                    "(set SCALEWAY_API_KEY + MISTRAL_API_KEY)"
                )
            resp = await ai_safety.safety_classify(
                SafetyRequest.model_validate(payload),
                llm=self._safety_llm,
                fallback_llm=self._safety_fallback_llm,
            )
            return resp.model_dump(mode="json")

    async def transcribe(self, *, file_name: str, audio_bytes: bytes) -> dict[str, Any]:
        from redream_ai.api import audio as ai_audio

        async with _boundary("transcribe", _TRANSCRIBE_TIMEOUT):
            resp = await ai_audio.transcribe(file_name=file_name, audio_bytes=audio_bytes)
            return resp.model_dump(mode="json")

    async def rehearsal_generate(self, payload: dict[str, Any]) -> dict[str, Any]:
        from redream_ai.api import audio as ai_audio
        from redream_ai.models import RehearsalRequest

        async with _boundary("rehearsal_generate", _REHEARSAL_TIMEOUT):
            resp = await ai_audio.rehearsal_generate(RehearsalRequest.model_validate(payload))
            return resp.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Module-level dependency hook (overridden in tests).
# ---------------------------------------------------------------------------


_default_client: BaseAiClient | None = None


def get_ai_client() -> BaseAiClient:
    """FastAPI dependency. Returns the process-wide AI client; the first call
    (the app's lifespan, at startup) builds it and logs its readiness."""
    global _default_client
    if _default_client is None:
        client = InProcessAiClient()
        client.log_readiness()
        _default_client = client
    return _default_client
