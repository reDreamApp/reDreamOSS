"""STT — transcribe dream voice journals (ADR 0010).

One provider, from configuration: a multipart ``POST
{STT_BASE_URL}/audio/transcriptions`` with ``model``, ``file`` and, when the
caller knows it, ``language`` — the OpenAI-compatible shape that OpenAI,
Mistral La Plateforme and Scaleway Generative APIs all accept. STT_BASE_URL /
STT_API_KEY fall back to LLM_BASE_URL / LLM_API_KEY; without STT_MODEL
transcription is off (`settings.stt_enabled`) and the backend answers 503
``voice_unavailable`` before anything reaches this module.

Privacy: raw voice recordings are the app's most sensitive payload (Art. 9
content in an identifiable voice). Pick the provider for its retention terms,
not only its quality.

Storage policy: the source audio is NOT persisted. Transcribe, return text,
discard bytes.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from ..config import STT_REQUIRED, settings
from ..llm.base import LlmUnavailableError
from ..models import Language, TranscribeResponse

log = logging.getLogger(__name__)

# Audio upload + inference is slow; chat-style short timeouts would false-fail.
_TIMEOUT = 120.0
# One retry on transient errors (429/5xx/network). Fewer attempts than the
# chat agents' — every retry re-uploads the whole recording.
_RETRY_DELAY = 1.0
_ATTEMPTS = 2

# Test seam: tests install an httpx.MockTransport here; None means real HTTP.
_transport: httpx.AsyncBaseTransport | None = None

# Whisper reports full language names ("english"), Voxtral ISO codes ("en"),
# some providers a region ("en-US"). The wire enum is en/de only — anything
# else is best-effort None.
_LANGUAGE_MAP = {
    "en": Language.EN,
    "english": Language.EN,
    "de": Language.DE,
    "german": Language.DE,
    "deutsch": Language.DE,
}


def _detected_language(payload: dict[str, Any]) -> Language | None:
    raw = payload.get("language")
    if raw is None:
        # OpenAI's gpt-transcribe reports a list instead: [{"code": "de"}].
        languages = payload.get("languages")
        if isinstance(languages, list) and languages and isinstance(languages[0], dict):
            raw = languages[0].get("code")
    if not isinstance(raw, str):
        return None
    key = raw.strip().lower().replace("_", "-")
    return _LANGUAGE_MAP.get(key) or _LANGUAGE_MAP.get(key.split("-")[0])


async def transcribe(
    *, audio_bytes: bytes, file_name: str, language: Language | None = None
) -> TranscribeResponse:
    """Transcribe one recording.

    Raises LlmUnavailableError (-> AIServiceError in the backend) when STT is
    off, on a hard 4xx (at once) or once the transient retries are used up.
    """
    if not settings.stt_enabled:
        raise LlmUnavailableError(f"speech input is off (set {STT_REQUIRED})")

    url = f"{settings.stt_base}/audio/transcriptions"
    data = {"model": settings.stt_model.strip()}
    if language is not None:
        data["language"] = language.value

    last_exc: Exception | None = None
    async with httpx.AsyncClient(timeout=_TIMEOUT, transport=_transport) as client:
        for attempt in range(_ATTEMPTS):
            try:
                r = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {settings.stt_key}"},
                    data=data,
                    files={"file": (file_name, audio_bytes)},
                )
            except httpx.HTTPError as exc:
                last_exc = exc
            else:
                if r.status_code < 300:
                    return _parse(r)
                last_exc = LlmUnavailableError(f"STT {r.status_code}: {r.text[:200]}")
                if r.status_code != 429 and r.status_code < 500:
                    # Bad key, unknown model, unsupported format: retrying
                    # won't help.
                    raise last_exc
            if attempt < _ATTEMPTS - 1:
                log.warning("stt.retry attempt=%d error=%s", attempt + 1, last_exc)
                await asyncio.sleep(_RETRY_DELAY)
    assert last_exc is not None
    if isinstance(last_exc, LlmUnavailableError):
        raise last_exc
    raise LlmUnavailableError(f"STT failed: {type(last_exc).__name__}: {last_exc}") from last_exc


def _parse(r: httpx.Response) -> TranscribeResponse:
    try:
        payload = r.json()
    except ValueError as exc:
        raise LlmUnavailableError("STT returned no JSON") from exc
    if not isinstance(payload, dict):
        raise LlmUnavailableError("STT returned no JSON object")
    return TranscribeResponse(
        text=str(payload.get("text") or ""),
        language_detected=_detected_language(payload),
    )
