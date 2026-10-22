"""STT — transcribe dream voice journals (ADR 0010, amended 2026-07-09).

Provider topology mirrors the chat agents: **Whisper on Scaleway primary,
Voxtral Transcribe on direct Mistral fallback.** The choice is a privacy one,
not a quality one: raw voice recordings are the app's most sensitive payload
(Art. 9 content in an identifiable voice), and Scaleway Generative APIs are
zero-retention Paris inference, while Mistral La Plateforme retains inputs up
to 30 days for abuse monitoring until ZDR is granted.
Voxtral on Scaleway is not an option — EOL there 2026-08-01.

Both providers speak the OpenAI-compatible ``POST /v1/audio/transcriptions``
multipart shape, so one request helper serves both.

Storage policy: the source audio is NOT persisted. Transcribe, return text,
discard bytes. If users later want playback of their own narration, that's a
deliberate scope expansion.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from ..config import settings
from ..llm.base import LlmUnavailableError
from ..models import Language, TranscribeResponse

log = logging.getLogger(__name__)

MISTRAL_TRANSCRIPTIONS_URL = "https://api.mistral.ai/v1/audio/transcriptions"


def _scaleway_transcriptions_url() -> str:
    # Same rule as core/agent.py: a project-restricted key must name its
    # project in the URL (SCALEWAY_PROJECT_ID); without it Scaleway uses the
    # organization's default project.
    project = f"/{settings.scaleway_project_id}" if settings.scaleway_project_id else ""
    return f"https://api.scaleway.ai{project}/v1/audio/transcriptions"



# Audio upload + inference is slow; chat-style short timeouts would false-fail.
_TIMEOUT = 120.0
# One retry per provider on transient errors (429/5xx/network). Fewer attempts
# than the chat agents' 4 — every retry re-uploads the whole recording.
_RETRY_DELAY = 1.0
_ATTEMPTS = 2

# Test seam: tests install an httpx.MockTransport here; None means real HTTP.
_transport: httpx.AsyncBaseTransport | None = None

# Whisper reports full language names ("english"); Voxtral reports ISO codes.
# The wire enum is en/de only — anything else is best-effort None.
_LANGUAGE_MAP = {
    "en": Language.EN,
    "english": Language.EN,
    "de": Language.DE,
    "german": Language.DE,
}


def _detected_language(payload: dict[str, Any]) -> Language | None:
    raw = payload.get("language")
    if not isinstance(raw, str):
        return None
    return _LANGUAGE_MAP.get(raw.strip().lower())


async def _transcribe_via(
    *,
    provider: str,
    url: str,
    api_key: str,
    model: str,
    audio_bytes: bytes,
    file_name: str,
) -> TranscribeResponse:
    """One provider attempt (with transient retries). Raises on any failure —
    the caller decides whether a fallback exists."""
    if not api_key:
        raise LlmUnavailableError(f"{provider} API key not configured")

    last_exc: Exception | None = None
    async with httpx.AsyncClient(timeout=_TIMEOUT, transport=_transport) as client:
        for attempt in range(_ATTEMPTS):
            try:
                r = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {api_key}"},
                    data={"model": model},
                    files={"file": (file_name, audio_bytes)},
                )
            except httpx.HTTPError as exc:
                last_exc = exc
            else:
                if r.status_code < 300:
                    payload = r.json()
                    return TranscribeResponse(
                        text=str(payload.get("text", "")),
                        language_detected=_detected_language(payload),
                    )
                if r.status_code == 429 or r.status_code >= 500:
                    last_exc = LlmUnavailableError(
                        f"{provider} STT {r.status_code}: {r.text[:200]}"
                    )
                else:
                    # Hard 4xx (bad key, model EOL'd, unsupported format):
                    # retrying is pointless — surface immediately so the
                    # caller can fall back.
                    raise LlmUnavailableError(f"{provider} STT {r.status_code}: {r.text[:200]}")
            if attempt < _ATTEMPTS - 1:
                log.warning(
                    "stt.retry provider=%s attempt=%d error=%s",
                    provider,
                    attempt + 1,
                    last_exc,
                )
                await asyncio.sleep(_RETRY_DELAY)
    assert last_exc is not None
    raise last_exc


async def transcribe(*, audio_bytes: bytes, file_name: str) -> TranscribeResponse:
    """Transcribe: Whisper on Scaleway, falling back to Voxtral on Mistral.

    Raises LlmUnavailableError (-> HTTP 503 via main.py's handler) only when
    both providers fail.
    """
    try:
        return await _transcribe_via(
            provider="scaleway",
            url=_scaleway_transcriptions_url(),
            api_key=settings.scaleway_api_key,
            model=settings.whisper_stt_model,
            audio_bytes=audio_bytes,
            file_name=file_name,
        )
    except Exception as primary_exc:
        log.warning(
            "stt.primary_failed model=%s error=%s — falling back to %s on Mistral",
            settings.whisper_stt_model,
            primary_exc,
            settings.voxtral_stt_model,
        )
        try:
            return await _transcribe_via(
                provider="mistral",
                url=MISTRAL_TRANSCRIPTIONS_URL,
                api_key=settings.mistral_api_key,
                model=settings.voxtral_stt_model,
                audio_bytes=audio_bytes,
                file_name=file_name,
            )
        except LlmUnavailableError:
            raise
        except Exception as fallback_exc:
            raise LlmUnavailableError(f"STT fallback failed: {fallback_exc}") from fallback_exc
