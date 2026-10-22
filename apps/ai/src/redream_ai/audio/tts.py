"""Voxtral TTS — render the rewritten dream as rehearsal audio (ADR 0010).

Contract (what the backend's `ai_client.rehearsal_generate` calls):
    POST /rehearsal/generate {dream_id, user_id, rewritten_dream, language}
    -> {audio_url, script, duration_estimate, voice_id, cache_hit}

The MP3 gets a Xing header (`mp3.py`, so players report its real length) and
goes straight to Supabase Storage with the service-role key; the backend only
stores the signed URL and never sees the audio bytes.

Object path: ``users/{user_id}/{dream_id}/{language}-{hash}.mp3``. The hash
covers script, language and voice, so an unchanged dream re-uses its file
(``cache_hit``) and an edited one gets a new object. The per-user prefix is
what the backend's GDPR purge deletes.

Voice: Mistral offers preset voices (GET /v1/audio/voices) and accepts their
slug as ``voice_id``; a cloned voice id works the same way. Requests without a
voice are rejected by the API. English uses ``gb_jane_neutral``, German the
preset "Heidi" (``voice_481``, 2026-10-21), which only the speech v3 model
accepts. A new voice changes the hash, so a dream gets new audio in it.

Privacy: Scaleway has no TTS, so the rewritten dream (Art. 9 content) goes to
Mistral La Plateforme (30-day abuse-monitoring retention until ZDR is
granted).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import math
from typing import Any

import httpx

from ..config import settings
from ..llm.base import LlmUnavailableError
from ..models import Language, RehearsalRequest, RehearsalResponse
from .mp3 import add_seek_header

log = logging.getLogger(__name__)

MISTRAL_SPEECH_URL = "https://api.mistral.ai/v1/audio/speech"

# Synthesizing a full rewritten dream takes far longer than a chat call.
_TIMEOUT = 60.0
_RETRY_DELAY = 1.0
_ATTEMPTS = 2

# Calm reading pace for the duration estimate.
_WORDS_PER_SECOND = 2.4

# Test seam: tests install an httpx.MockTransport here; None means real HTTP.
_transport: httpx.AsyncBaseTransport | None = None


async def generate_rehearsal(req: RehearsalRequest) -> RehearsalResponse:
    """Render `req.rewritten_dream` via Voxtral, upload it, return a signed URL.

    Raises LlmUnavailableError (-> HTTP 503 via main.py's handler) when the
    TTS provider or Storage is unavailable or not configured.
    """
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise LlmUnavailableError("rehearsal storage not configured")
    voice_id = _voice_for(req.language)
    if not voice_id:
        raise LlmUnavailableError(f"no Voxtral voice configured for {req.language.value}")

    script = req.rewritten_dream.strip()
    path = object_path(req, script, voice_id)

    async with httpx.AsyncClient(timeout=_TIMEOUT, transport=_transport) as client:
        storage = _Storage(client)
        cache_hit = await storage.exists(path)
        if not cache_hit:
            audio = await _synthesize(client, text=script, voice_id=voice_id)
            # Without it players misreport the length and seek inaccurately.
            await storage.upload(path, add_seek_header(audio))
        url = await storage.signed_url(path, settings.rehearsal_url_ttl_seconds)

    return RehearsalResponse(
        audio_url=url,
        script=script,
        duration_estimate=estimate_duration(script),
        voice_id=voice_id,
        cache_hit=cache_hit,
    )


def _voice_for(language: Language) -> str:
    return {
        Language.EN: settings.voxtral_voice_id_en,
        Language.DE: settings.voxtral_voice_id_de,
    }[language].strip()


def object_path(req: RehearsalRequest, script: str, voice_id: str) -> str:
    digest = hashlib.sha256(f"{script}|{req.language.value}|{voice_id}".encode()).hexdigest()
    return f"users/{req.user_id}/{req.dream_id}/v2-{req.language.value}-{digest[:24]}.mp3"


def estimate_duration(script: str) -> str:
    seconds = math.ceil(len(script.split()) / _WORDS_PER_SECOND)
    return f"{seconds // 60}:{seconds % 60:02d}"


async def _synthesize(client: httpx.AsyncClient, *, text: str, voice_id: str) -> bytes:
    if not settings.mistral_api_key:
        raise LlmUnavailableError("Mistral API key not configured")
    body: dict[str, Any] = {
        "model": settings.voxtral_tts_model,
        "input": text,
        "voice_id": voice_id,
        "response_format": "mp3",
    }
    last_exc: Exception | None = None
    for attempt in range(_ATTEMPTS):
        try:
            r = await client.post(
                MISTRAL_SPEECH_URL,
                headers={"Authorization": f"Bearer {settings.mistral_api_key}"},
                json=body,
            )
        except httpx.HTTPError as exc:
            last_exc = exc
        else:
            if r.status_code < 300:
                audio = r.json().get("audio_data")
                if not isinstance(audio, str) or not audio:
                    raise LlmUnavailableError("Voxtral TTS returned no audio_data")
                return base64.b64decode(audio)
            last_exc = LlmUnavailableError(f"Voxtral TTS {r.status_code}: {r.text[:200]}")
            if r.status_code != 429 and r.status_code < 500:
                raise last_exc  # bad key/voice/model: retrying won't help
        if attempt < _ATTEMPTS - 1:
            log.warning("tts.retry attempt=%d error=%s", attempt + 1, last_exc)
            await asyncio.sleep(_RETRY_DELAY)
    assert last_exc is not None
    if isinstance(last_exc, LlmUnavailableError):
        raise last_exc
    raise LlmUnavailableError(f"Voxtral TTS failed: {last_exc}") from last_exc


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
