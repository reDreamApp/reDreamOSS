"""STT: Whisper-on-Scaleway primary + Voxtral-on-Mistral fallback (ADR 0010).

Fully offline: an httpx.MockTransport goes in via the module's `_transport`
seam, and retry sleeps are zeroed. Request shapes (multipart file + model
field, bearer auth) are asserted against the OpenAI-compatible contract both
providers implement.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

import redream_ai.audio.stt as stt
from redream_ai.llm.base import LlmUnavailableError
from redream_ai.main import app
from redream_ai.models import Language


@pytest.fixture(autouse=True)
def _fast_and_keyed(monkeypatch):
    monkeypatch.setattr(stt, "_RETRY_DELAY", 0.0)
    monkeypatch.setattr(stt.settings, "scaleway_api_key", "scw-test")
    monkeypatch.setattr(stt.settings, "scaleway_project_id", "")
    monkeypatch.setattr(stt.settings, "mistral_api_key", "mst-test")


def _install_transport(monkeypatch, handler) -> list[httpx.Request]:
    requests: list[httpx.Request] = []

    def _logging_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    monkeypatch.setattr(stt, "_transport", httpx.MockTransport(_logging_handler))
    return requests


def test_stt_model_pins():
    """ADR 0010 (2026-07-09 amendment): both STT models are pinned — a silent
    default change here is a provider/model change and needs the ADR updated."""
    from redream_ai.config import Settings

    # Class defaults, not a Settings() instance — the suite's process env
    # carries the developer's real .env (loaded by the core's load_dotenv).
    fields = Settings.model_fields
    assert fields["whisper_stt_model"].default == "openai/whisper-large-v3:fp16"
    assert fields["voxtral_stt_model"].default == "voxtral-mini-2602"


async def test_scaleway_primary_success(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.scaleway.ai"
        assert request.headers["Authorization"] == "Bearer scw-test"
        body = request.read()
        assert stt.settings.whisper_stt_model.encode() in body  # model form field
        assert b"dream.mp3" in body  # multipart file name
        return httpx.Response(
            200,
            json={"text": "I was chased through a forest.", "language": "english"},
        )

    requests = _install_transport(monkeypatch, handler)

    resp = await stt.transcribe(audio_bytes=b"fake-audio", file_name="dream.mp3")

    assert resp.text == "I was chased through a forest."
    assert resp.language_detected == Language.EN  # "english" mapped to enum
    assert len(requests) == 1  # no fallback touched


async def test_scaleway_project_id_scopes_the_url(monkeypatch):
    monkeypatch.setattr(stt.settings, "scaleway_project_id", "proj-123")
    requests = _install_transport(
        monkeypatch, lambda request: httpx.Response(200, json={"text": "ok", "language": "english"})
    )

    await stt.transcribe(audio_bytes=b"fake-audio", file_name="dream.mp3")

    assert str(requests[0].url) == "https://api.scaleway.ai/proj-123/v1/audio/transcriptions"


async def test_short_language_code_is_mapped(monkeypatch):
    _install_transport(
        monkeypatch,
        lambda request: httpx.Response(
            200, json={"text": "Ich wurde durch einen Wald gejagt.", "language": "de"}
        ),
    )

    resp = await stt.transcribe(audio_bytes=b"x", file_name="a.wav")

    assert resp.language_detected == Language.DE


async def test_hard_4xx_falls_back_without_retry(monkeypatch):
    """A 404 (e.g. model EOL'd) must not burn a retry — straight to Mistral."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.scaleway.ai":
            return httpx.Response(404, text="model not found")
        assert request.url.host == "api.mistral.ai"
        assert request.headers["Authorization"] == "Bearer mst-test"
        assert stt.settings.voxtral_stt_model.encode() in request.read()
        return httpx.Response(200, json={"text": "fallback text", "language": "en"})

    requests = _install_transport(monkeypatch, handler)

    resp = await stt.transcribe(audio_bytes=b"x", file_name="a.mp3")

    assert resp.text == "fallback text"
    assert [r.url.host for r in requests] == ["api.scaleway.ai", "api.mistral.ai"]


async def test_transient_5xx_retries_then_falls_back(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.scaleway.ai":
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json={"text": "ok"})

    requests = _install_transport(monkeypatch, handler)

    resp = await stt.transcribe(audio_bytes=b"x", file_name="a.mp3")

    assert resp.text == "ok"
    hosts = [r.url.host for r in requests]
    assert hosts == ["api.scaleway.ai", "api.scaleway.ai", "api.mistral.ai"]


async def test_missing_scaleway_key_uses_fallback(monkeypatch):
    monkeypatch.setattr(stt.settings, "scaleway_api_key", "")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.mistral.ai"
        return httpx.Response(200, json={"text": "ok"})

    requests = _install_transport(monkeypatch, handler)

    resp = await stt.transcribe(audio_bytes=b"x", file_name="a.mp3")
    assert resp.text == "ok"
    assert len(requests) == 1


async def test_both_providers_down_raises_unavailable(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    _install_transport(monkeypatch, handler)

    with pytest.raises(LlmUnavailableError):
        await stt.transcribe(audio_bytes=b"x", file_name="a.mp3")


def test_route_maps_unavailable_to_503(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    _install_transport(monkeypatch, handler)

    with TestClient(app) as tc:
        r = tc.post(
            "/audio/transcribe",
            files={"file": ("dream.mp3", b"fake-audio", "audio/mpeg")},
        )
    assert r.status_code == 503
    assert "Retry-After" in r.headers


def test_route_happy_path(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "hello", "language": "en"})

    _install_transport(monkeypatch, handler)

    with TestClient(app) as tc:
        r = tc.post(
            "/audio/transcribe",
            files={"file": ("dream.mp3", b"fake-audio", "audio/mpeg")},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["text"] == "hello"
    assert body["language_detected"] == "en"
