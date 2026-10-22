"""TTS: Voxtral speech + Supabase Storage cache (ADR 0010).

Fully offline: an httpx.MockTransport goes in via the module's `_transport`
seam and stands in for both Mistral and Supabase Storage.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import redream_ai.audio.tts as tts
from redream_ai.llm.base import LlmUnavailableError
from redream_ai.main import app
from redream_ai.models import Language, RehearsalRequest

_AUDIO = b"ID3-fake-mp3"


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(tts, "_RETRY_DELAY", 0.0)
    s = tts.settings
    monkeypatch.setattr(s, "mistral_api_key", "mst-test")
    monkeypatch.setattr(s, "supabase_url", "http://kong:8000")
    monkeypatch.setattr(s, "supabase_public_url", "https://supabase.example.com")
    monkeypatch.setattr(s, "supabase_service_role_key", "srk")
    monkeypatch.setattr(s, "supabase_storage_bucket", "rehearsal-audio")
    monkeypatch.setattr(s, "voxtral_tts_model", "voxtral-mini-tts-3-0")
    monkeypatch.setattr(s, "voxtral_voice_id_en", "gb_jane_neutral")
    monkeypatch.setattr(s, "voxtral_voice_id_de", "voice_481")


def _req(text: str = "You walk home with a warm lantern.", lang: Language = Language.EN):
    return RehearsalRequest(dream_id="7", user_id="u-1", rewritten_dream=text, language=lang)


class _Fake:
    """Mistral speech + a dict-backed Storage bucket."""

    def __init__(self, *, speech_status: int = 200, audio: bytes = _AUDIO) -> None:
        self.objects: dict[str, bytes] = {}
        self.speech_status = speech_status
        self.audio = audio
        self.speech_calls: list[dict[str, object]] = []
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url == tts.MISTRAL_SPEECH_URL:
            assert request.headers["Authorization"] == "Bearer mst-test"
            self.speech_calls.append(json.loads(request.content))
            if self.speech_status != 200:
                return httpx.Response(self.speech_status, text="nope")
            return httpx.Response(200, json={"audio_data": base64.b64encode(self.audio).decode()})
        assert request.headers["apikey"] == "srk"
        prefix = "http://kong:8000/storage/v1/object/"
        assert url.startswith(prefix), url
        rest = url[len(prefix) :]
        if rest == "list/rehearsal-audio":
            body = json.loads(request.content)
            folder, name = body["prefix"], body["search"]
            hits = [{"name": name}] if f"{folder}/{name}" in self.objects else []
            return httpx.Response(200, json=hits)
        if rest.startswith("sign/rehearsal-audio/"):
            path = rest[len("sign/rehearsal-audio/") :]
            return httpx.Response(
                200, json={"signedURL": f"/object/sign/rehearsal-audio/{path}?token=t"}
            )
        if rest.startswith("rehearsal-audio/"):
            assert request.headers["x-upsert"] == "true"
            assert request.headers["Content-Type"] == "audio/mpeg"
            self.objects[rest[len("rehearsal-audio/") :]] = request.content
            return httpx.Response(200, json={"Key": rest})
        return httpx.Response(404)


def _install(monkeypatch, fake: _Fake) -> _Fake:
    monkeypatch.setattr(tts, "_transport", httpx.MockTransport(fake))
    return fake


def test_voice_and_model_defaults():
    """Voxtral TTS rejects requests without a voice: the defaults must name one."""
    from redream_ai.config import Settings

    fields = Settings.model_fields
    assert fields["voxtral_tts_model"].default == "voxtral-mini-tts-3-0"
    assert fields["voxtral_voice_id_en"].default == "gb_jane_neutral"
    # The German preset "Heidi"; speech v1 (voxtral-mini-tts-2603) rejects it.
    assert fields["voxtral_voice_id_de"].default == "voice_481"


async def test_generates_uploads_and_signs(monkeypatch):
    fake = _install(monkeypatch, _Fake())

    resp = await tts.generate_rehearsal(_req())

    assert resp.cache_hit is False
    assert resp.voice_id == "gb_jane_neutral"
    assert resp.script == "You walk home with a warm lantern."
    assert resp.duration_estimate == "0:03"
    (path,) = fake.objects
    assert path.startswith("users/u-1/7/v2-en-") and path.endswith(".mp3")
    assert fake.objects[path] == _AUDIO
    assert fake.speech_calls == [
        {
            "model": "voxtral-mini-tts-3-0",
            "input": "You walk home with a warm lantern.",
            "voice_id": "gb_jane_neutral",
            "response_format": "mp3",
        }
    ]
    # Signed URL points at the public host, not the internal compose address.
    assert resp.audio_url == (
        f"https://supabase.example.com/storage/v1/object/sign/rehearsal-audio/{path}?token=t"
    )


async def test_stored_audio_gets_a_seek_header(monkeypatch):
    """Voxtral's MP3 carries no length header; players misreport it without one."""
    # MPEG-2 Layer III frames, 80 kbit/s, 22.05 kHz mono.
    audio = ((0xFFF390C0).to_bytes(4, "big") + bytes(257)) * 4
    fake = _install(monkeypatch, _Fake(audio=audio))

    await tts.generate_rehearsal(_req())

    (stored,) = fake.objects.values()
    assert b"Xing" in stored
    assert stored.endswith(audio)


async def test_unchanged_dream_reuses_the_stored_file(monkeypatch):
    fake = _install(monkeypatch, _Fake())
    first = await tts.generate_rehearsal(_req())
    second = await tts.generate_rehearsal(_req())

    assert second.cache_hit is True
    assert second.audio_url == first.audio_url
    assert len(fake.speech_calls) == 1


async def test_edited_dream_or_language_gets_a_new_file(monkeypatch):
    fake = _install(monkeypatch, _Fake())
    await tts.generate_rehearsal(_req())
    await tts.generate_rehearsal(_req("You fly over the forest."))
    await tts.generate_rehearsal(_req(lang=Language.DE))

    assert len(fake.objects) == 3
    assert len(fake.speech_calls) == 3
    assert fake.speech_calls[2]["voice_id"] == "voice_481"


async def test_new_voice_gets_a_new_file(monkeypatch):
    """Dreams rendered in the old German voice get new audio in the new one."""
    fake = _install(monkeypatch, _Fake())
    monkeypatch.setattr(tts.settings, "voxtral_voice_id_de", "gb_jane_neutral")
    await tts.generate_rehearsal(_req(lang=Language.DE))
    monkeypatch.setattr(tts.settings, "voxtral_voice_id_de", "voice_481")
    second = await tts.generate_rehearsal(_req(lang=Language.DE))

    assert second.cache_hit is False
    assert len(fake.objects) == 2
    assert [c["voice_id"] for c in fake.speech_calls] == ["gb_jane_neutral", "voice_481"]


async def test_hard_tts_error_is_not_retried(monkeypatch):
    fake = _install(monkeypatch, _Fake(speech_status=400))
    with pytest.raises(LlmUnavailableError, match="Voxtral TTS 400"):
        await tts.generate_rehearsal(_req())
    assert len(fake.speech_calls) == 1
    assert fake.objects == {}


async def test_transient_tts_error_is_retried(monkeypatch):
    fake = _install(monkeypatch, _Fake(speech_status=503))
    with pytest.raises(LlmUnavailableError):
        await tts.generate_rehearsal(_req())
    assert len(fake.speech_calls) == tts._ATTEMPTS


async def test_transient_tts_error_recovers_on_retry(monkeypatch):
    fake = _Fake(speech_status=503)

    def recover(request):
        if len(fake.speech_calls) == 1:
            fake.speech_status = 200
        return fake(request)

    monkeypatch.setattr(tts, "_transport", httpx.MockTransport(recover))
    response = await tts.generate_rehearsal(_req())
    assert response.cache_hit is False
    assert len(fake.speech_calls) == 2
    assert len(fake.objects) == 1


async def test_missing_storage_config_is_unavailable(monkeypatch):
    monkeypatch.setattr(tts.settings, "supabase_service_role_key", "")
    with pytest.raises(LlmUnavailableError, match="storage not configured"):
        await tts.generate_rehearsal(_req())


def test_route_maps_unavailable_to_503(monkeypatch):
    _install(monkeypatch, _Fake(speech_status=400))
    client = TestClient(app)
    res = client.post(
        "/rehearsal/generate",
        json={"dream_id": "7", "user_id": "u-1", "rewritten_dream": "x", "language": "en"},
    )
    assert res.status_code == 503
    assert res.headers["Retry-After"] == "10"


def test_route_rejects_empty_dream():
    client = TestClient(app)
    res = client.post(
        "/rehearsal/generate",
        json={"dream_id": "7", "user_id": "u-1", "rewritten_dream": "  ", "language": "en"},
    )
    assert res.status_code == 400


@pytest.mark.parametrize(("words", "expected"), [(1, "0:01"), (24, "0:10"), (360, "2:30")])
def test_duration_estimate(words: int, expected: str):
    assert tts.estimate_duration(" ".join(["word"] * words)) == expected
