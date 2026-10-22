"""TTS — render the rewritten dream as rehearsal audio (ADR 0010).

Contract (what the backend's `ai_client.rehearsal_generate` calls, in-process
via `api.audio.rehearsal_generate`):
    {dream_id, user_id, rewritten_dream, language}
    -> {audio_url, script, duration_estimate, voice_id, cache_hit}

Speech comes from one provider, from configuration: ``POST
{TTS_BASE_URL}/audio/speech`` (base URL and key fall back to LLM_*), in the
request/response shape TTS_API_STYLE names:

- ``openai``: ``{model, input, voice, response_format: "mp3"}``, the MP3
  bytes as the response body (OpenAI and the servers that copy its API).
- ``mistral``: ``{model, input, voice_id, response_format: "mp3"}``, the MP3
  base64-encoded in the JSON field ``audio_data`` (Mistral La Plateforme).

The whole script goes in one request, so a provider's input cap is the
script's (OpenAI: 4096 characters; a longer one is a 400, raised at once).

TTS_VOICE serves every language unless TTS_VOICE_EN / TTS_VOICE_DE override
it. Without TTS_MODEL, or while a language has no voice, speech output is off
(`settings.tts_enabled`) and the backend answers 503 ``voice_unavailable``
before anything reaches this module.

The MP3 gets a Xing header (`mp3.py`, so players report its real length) and
goes straight to Supabase Storage with the service-role key; the backend only
stores the signed URL and never sees the audio bytes.

Object path: ``users/{user_id}/{dream_id}/v2-{language}-{hash}.mp3``. The hash
covers model, voice, language and script, so an unchanged dream re-uses its
file (``cache_hit``) and an edited dream, or one in a new voice or model, gets
a new object. The per-user prefix is what the backend's GDPR purge deletes;
the ``v2-`` prefix is what the backend's URL refresh expects of current files.

Privacy: the rewritten dream (Art. 9 content) goes to the TTS provider; pick
it for its retention terms.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import math
from typing import Any

import httpx

from ..config import TTS_REQUIRED, settings
from ..llm.base import LlmUnavailableError
from ..models import Language, RehearsalRequest, RehearsalResponse
from .mp3 import add_seek_header

log = logging.getLogger(__name__)

# Synthesizing a full rewritten dream takes far longer than a chat call.
_TIMEOUT = 60.0
_RETRY_DELAY = 1.0
_ATTEMPTS = 2

# Calm reading pace for the duration estimate.
_WORDS_PER_SECOND = 2.4

# Test seam: tests install an httpx.MockTransport here; None means real HTTP.
_transport: httpx.AsyncBaseTransport | None = None


async def generate_rehearsal(req: RehearsalRequest) -> RehearsalResponse:
    """Render `req.rewritten_dream`, upload it, return a signed URL.

    Raises LlmUnavailableError (-> AIServiceError in the backend) when speech
    output is off, or the TTS provider or Storage is unavailable or not
    configured.
    """
    if not settings.tts_enabled:
        raise LlmUnavailableError(f"speech output is off (set {TTS_REQUIRED})")
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise LlmUnavailableError("rehearsal storage not configured")
    voice = settings.tts_voice_for(req.language.value)
    model = settings.tts_model.strip()

    script = req.rewritten_dream.strip()
    path = object_path(req, script, model=model, voice=voice)

    async with httpx.AsyncClient(timeout=_TIMEOUT, transport=_transport) as client:
        storage = _Storage(client)
        cache_hit = await storage.exists(path)
        if not cache_hit:
            audio = await synthesize(script, req.language)
            # Without it players misreport the length and seek inaccurately.
            await storage.upload(path, add_seek_header(audio))
        url = await storage.signed_url(path, settings.rehearsal_url_ttl_seconds)

    return RehearsalResponse(
        audio_url=url,
        script=script,
        duration_estimate=estimate_duration(script),
        voice_id=voice,
        cache_hit=cache_hit,
    )


def object_path(req: RehearsalRequest, script: str, *, model: str, voice: str) -> str:
    """The Storage object for this rendering; a new model, voice, language or
    script gives a new name."""
    lang = req.language.value
    digest = hashlib.sha256(f"{model}|{voice}|{lang}|{script}".encode()).hexdigest()
    # Keep "v2-": the backend's URL refresh moves any other name as legacy.
    return f"users/{req.user_id}/{req.dream_id}/v2-{lang}-{digest[:24]}.mp3"


def estimate_duration(script: str) -> str:
    seconds = math.ceil(len(script.split()) / _WORDS_PER_SECOND)
    return f"{seconds // 60}:{seconds % 60:02d}"


async def synthesize(script: str, language: Language) -> bytes:
    """One rendering of `script` in `language`'s voice, as MP3 bytes.

    Two attempts on 429/5xx/connection errors; any other 4xx is raised at
    once. Raises LlmUnavailableError.
    """
    if not settings.tts_enabled:
        raise LlmUnavailableError(f"speech output is off (set {TTS_REQUIRED})")
    style = settings.tts_api_style
    voice_field = "voice_id" if style == "mistral" else "voice"
    body: dict[str, Any] = {
        "model": settings.tts_model.strip(),
        "input": script,
        voice_field: settings.tts_voice_for(language.value),
        "response_format": "mp3",
    }
    url = f"{settings.tts_base}/audio/speech"
    last_exc: Exception | None = None
    async with httpx.AsyncClient(timeout=_TIMEOUT, transport=_transport) as client:
        for attempt in range(_ATTEMPTS):
            try:
                r = await client.post(
                    url, headers={"Authorization": f"Bearer {settings.tts_key}"}, json=body
                )
            except httpx.HTTPError as exc:
                last_exc = exc
            else:
                if r.status_code < 300:
                    return _audio(r, style)
                last_exc = LlmUnavailableError(f"TTS {r.status_code}: {r.text[:200]}")
                if r.status_code != 429 and r.status_code < 500:
                    raise last_exc  # bad key/voice/model: retrying won't help
            if attempt < _ATTEMPTS - 1:
                log.warning("tts.retry attempt=%d error=%s", attempt + 1, last_exc)
                await asyncio.sleep(_RETRY_DELAY)
    assert last_exc is not None
    if isinstance(last_exc, LlmUnavailableError):
        raise last_exc
    raise LlmUnavailableError(f"TTS failed: {type(last_exc).__name__}: {last_exc}") from last_exc


def _audio(r: httpx.Response, style: str) -> bytes:
    """The MP3 in a successful speech response."""
    if style == "mistral":
        try:
            payload = r.json()
            encoded = payload.get("audio_data") if isinstance(payload, dict) else None
            audio = base64.b64decode(encoded) if isinstance(encoded, str) else b""
        except ValueError as exc:  # no JSON, or not base64 (binascii.Error)
            raise LlmUnavailableError("TTS returned unreadable audio_data") from exc
        if not audio:
            raise LlmUnavailableError(
                "TTS returned no audio_data (TTS_API_STYLE=mistral; openai for raw audio)"
            )
        return audio
    if r.headers.get("content-type", "").startswith("application/json"):
        raise LlmUnavailableError(
            "TTS returned JSON, not audio (TTS_API_STYLE=openai; mistral for audio_data)"
        )
    if not r.content:
        raise LlmUnavailableError("TTS returned no audio")
    return r.content


class _Storage:
    """The three Supabase Storage REST calls TTS needs."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._base = settings.supabase_url.rstrip("/") + "/storage/v1"
        self._bucket = settings.supabase_storage_bucket
        key = settings.supabase_service_role_key
        self._auth = {"Authorization": f"Bearer {key}", "apikey": key}

    async def exists(self, path: str) -> bool:
        folder, _, name = path.rpartition("/")
        r = await self._client.post(
            f"{self._base}/object/list/{self._bucket}",
            headers=self._auth,
            json={"prefix": folder, "search": name, "limit": 1},
        )
        _raise_for_storage(r, "list")
        return any(item.get("name") == name for item in r.json())

    async def upload(self, path: str, audio: bytes) -> None:
        r = await self._client.post(
            f"{self._base}/object/{self._bucket}/{path}",
            headers={**self._auth, "Content-Type": "audio/mpeg", "x-upsert": "true"},
            content=audio,
        )
        _raise_for_storage(r, "upload")

    async def signed_url(self, path: str, ttl_seconds: int) -> str:
        r = await self._client.post(
            f"{self._base}/object/sign/{self._bucket}/{path}",
            headers=self._auth,
            json={"expiresIn": ttl_seconds},
        )
        _raise_for_storage(r, "sign")
        payload = r.json()
        signed = payload.get("signedURL") or payload.get("signedUrl")
        if not isinstance(signed, str):
            raise LlmUnavailableError("storage sign returned no URL")
        # The AI container reaches Supabase on an internal address (kong);
        # the app needs the public host.
        public = (settings.supabase_public_url or settings.supabase_url).rstrip("/")
        return f"{public}/storage/v1{signed}"


def _raise_for_storage(r: httpx.Response, action: str) -> None:
    if r.status_code >= 300:
        raise LlmUnavailableError(f"storage {action} {r.status_code}: {r.text[:200]}")
