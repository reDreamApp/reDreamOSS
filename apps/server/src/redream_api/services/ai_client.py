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
- language_check    : user text + chat language → the language the chat
                      continues in (en|de); called before the classifier
- transcribe        : audio bytes → transcript (ADR 0010)
- rehearsal_generate: dream → audio_key (the MP3 under AUDIO_DIR) + script +
                      duration
- stt_enabled / tts_enabled: whether each half of speech is configured; the
                      routes answer 503 voice_unavailable while it is off,
                      and the client never calls STT/TTS then

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
# more: TTS has two 60s attempts plus writing the file.
_CHAT_TIMEOUT = 90.0
_TRANSCRIBE_TIMEOUT = 90.0
_REHEARSAL_TIMEOUT = 150.0
# The AI side gives its model call 8s; a check that takes longer keeps the
# language rather than holding up the turn.
_LANGUAGE_CHECK_TIMEOUT = 10.0

_CHAT_LANGUAGES = ("en", "de")

# The 503 detail of /audio/transcribe and /dreams/{id}/rehearsal while their
# half of speech is off.
VOICE_UNAVAILABLE = "voice_unavailable"

# Readiness warning when a half of speech is off because its base URL is on
# another host than LLM_BASE_URL's and its own key is empty: LLM_API_KEY is
# never sent to another provider (args: "input"/"output", "STT"/"TTS").
_BORROWED_KEY = (
    "speech %s off: %s_BASE_URL is on another host than LLM_BASE_URL's and its "
    "API key is empty — LLM_API_KEY is not sent there; set that provider's key "
    "(any value, e.g. none, for a server without auth)"
)


class AIServiceError(Exception):
    """Raised when the AI side fails, rejects the request or times out."""


class BaseAiClient(Protocol):
    """The chat/audio surface the backend depends on. Tests substitute a fake."""

    async def chat(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def chat_stream(self, payload: dict[str, Any]) -> AsyncGenerator[str, None]: ...
    async def safety_classify(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    async def language_check(self, message: str, language: str) -> str: ...
    async def transcribe(self, *, file_name: str, audio_bytes: bytes) -> dict[str, Any]: ...
    async def rehearsal_generate(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    @property
    def stt_enabled(self) -> bool: ...
    @property
    def tts_enabled(self) -> bool: ...


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
        from redream_ai.api.safety import build_safety_llm_client

        # Crisis classifier (ADR 0005): one client on the LLM_* endpoint, no
        # fallback, built once per process.
        self._safety_llm: LlmClient | None = build_safety_llm_client()

    def log_readiness(self) -> None:
        """Log what the AI side has ready. Importing the IRT core builds its
        agents and the SafetyCritic, so this also does that work up front."""
        from redream_ai.config import LLM_REQUIRED, STT_REQUIRED, TTS_REQUIRED, settings
        from redream_ai.core import irt_app

        if irt_app.routing_agent is None or irt_app.response_agent is None:
            log.warning("IRT agents UNCONFIGURED — set %s; chat turns fail (502)", LLM_REQUIRED)
        else:
            log.info(
                "IRT pipeline ready (agents=ok, safety_critic=%s)",
                "ok" if irt_app.safety_critic is not None else "disabled (fail-open)",
            )
        if self._safety_llm is None:
            log.warning(
                "crisis classifier UNCONFIGURED — set %s; chat turns fail (502)", LLM_REQUIRED
            )
        else:
            log.info("crisis classifier ready (model=%s)", settings.safety_model)
        # Speech is optional: off is an info, a model without the rest a warning.
        if settings.stt_enabled:
            log.info("speech input ready (model=%s)", settings.stt_model.strip())
        elif settings.stt_model.strip() and settings.stt_borrows_llm_key:
            log.warning(_BORROWED_KEY, "input", "STT")
        else:
            log.log(
                logging.WARNING if settings.stt_model.strip() else logging.INFO,
                "speech input off — set %s; transcribe answers 503",
                STT_REQUIRED,
            )
        if settings.tts_enabled:
            log.info(
                "speech output ready (model=%s, style=%s)",
                settings.tts_model.strip(),
                settings.tts_api_style,
            )
        elif settings.tts_model.strip() and settings.tts_borrows_llm_key:
            log.warning(_BORROWED_KEY, "output", "TTS")
        else:
            log.log(
                logging.WARNING if settings.tts_model.strip() else logging.INFO,
                "speech output off — set %s; rehearsal answers 503",
                TTS_REQUIRED,
            )

    @property
    def stt_enabled(self) -> bool:
        from redream_ai.config import settings

        return settings.stt_enabled

    @property
    def tts_enabled(self) -> bool:
        from redream_ai.config import settings

        return settings.tts_enabled

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
            if self._safety_llm is None:
                from redream_ai.config import LLM_REQUIRED

                raise AIServiceError(
                    f"safety_classify failed: crisis classifier not configured (set {LLM_REQUIRED})"
                )
            resp = await ai_safety.safety_classify(
                SafetyRequest.model_validate(payload), llm=self._safety_llm
            )
            return resp.model_dump(mode="json")

    async def language_check(self, message: str, language: str) -> str:
        """The language the chat continues in.

        Never raises: a failure, a timeout or an answer that is not en/de keeps
        `language`, so the turn always proceeds. The warning leaves out the
        message text (user content does not go into logs).
        """
        from redream_ai.api import language as ai_language
        from redream_ai.models import LanguageCheckRequest

        try:
            async with asyncio.timeout(_LANGUAGE_CHECK_TIMEOUT):
                resp = await ai_language.language_check(
                    LanguageCheckRequest.model_validate({"message": message, "language": language})
                )
            new = resp.language.value
        except Exception as exc:  # never raises: the turn must go on
            log.warning("language_check failed: %s — keeping %s", type(exc).__name__, language)
            return language
        if new not in _CHAT_LANGUAGES:
            log.warning("language_check returned an invalid language — keeping %s", language)
            return language
        return str(new)

    async def transcribe(self, *, file_name: str, audio_bytes: bytes) -> dict[str, Any]:
        from redream_ai.api import audio as ai_audio

        async with _boundary("transcribe", _TRANSCRIBE_TIMEOUT):
            if not self.stt_enabled:
                raise AIServiceError("transcribe failed: speech input is off")
            resp = await ai_audio.transcribe(file_name=file_name, audio_bytes=audio_bytes)
            return resp.model_dump(mode="json")

    async def rehearsal_generate(self, payload: dict[str, Any]) -> dict[str, Any]:
        from redream_ai.api import audio as ai_audio
        from redream_ai.models import RehearsalRequest

        async with _boundary("rehearsal_generate", _REHEARSAL_TIMEOUT):
            if not self.tts_enabled:
                raise AIServiceError("rehearsal_generate failed: speech output is off")
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
