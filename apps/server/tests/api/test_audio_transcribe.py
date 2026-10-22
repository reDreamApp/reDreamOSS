"""POST /api/v1/audio/transcribe — voice journaling proxy (ADR 0010)."""

from __future__ import annotations

from redream_api.services.ai_client import AIServiceError

from .conftest import FakeAiClient


def _upload(content: bytes = b"fake-audio"):
    return {"file": ("dream.m4a", content, "audio/mp4")}


async def test_transcribe_returns_text(client, fake_ai: FakeAiClient):
    fake_ai.queue_transcribe(
        {
            "text": "I was chased through a forest.",
            "language_detected": "en",
        }
    )

    r = await client.post("/api/v1/audio/transcribe", files=_upload())

    assert r.status_code == 200
    body = r.json()
    assert body["text"] == "I was chased through a forest."
    assert body["language_detected"] == "en"

    call = fake_ai.calls[0]
    assert call["method"] == "transcribe"
    assert call["file_name"] == "dream.m4a"


async def test_transcribe_ai_failure_is_502(client, fake_ai: FakeAiClient):
    fake_ai.queue_transcribe_error(AIServiceError("both providers down"))

    r = await client.post("/api/v1/audio/transcribe", files=_upload())

    assert r.status_code == 502


async def test_transcribe_oversized_audio_is_413(client, fake_ai: FakeAiClient, monkeypatch):
    from redream_api.api.v1.audio import routes as audio_routes

    monkeypatch.setattr(audio_routes, "MAX_AUDIO_BYTES", 10)

    r = await client.post("/api/v1/audio/transcribe", files=_upload(b"x" * 11))

    assert r.status_code == 413
    assert fake_ai.calls == []
