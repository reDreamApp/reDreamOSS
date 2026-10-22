"""STT: one provider from configuration (STT_BASE_URL / STT_API_KEY / STT_MODEL).

Fully offline: an httpx.MockTransport goes in via the module's `_transport`
seam, and retry sleeps are zeroed. Request shapes (multipart file + model
field, bearer auth) are asserted against the OpenAI-compatible
/audio/transcriptions contract.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import HTTPException

import redream_ai.api.audio as audio
import redream_ai.audio.stt as stt
from redream_ai.config import Settings
from redream_ai.llm.base import LlmUnavailableError
from redream_ai.models import Language

_BASE = "https://stt.example.test/v1"


@pytest.fixture(autouse=True)
def _fast_and_configured(monkeypatch):
    monkeypatch.setattr(stt, "_RETRY_DELAY", 0.0)
    s = stt.settings
    monkeypatch.setattr(s, "llm_base_url", "https://llm.example.test/v1")
    monkeypatch.setattr(s, "llm_api_key", "llm-key")
    monkeypatch.setattr(s, "stt_base_url", _BASE)
    monkeypatch.setattr(s, "stt_api_key", "stt-key")
    monkeypatch.setattr(s, "stt_model", "whisper-test")


def _install_transport(monkeypatch, handler) -> list[httpx.Request]:
    requests: list[httpx.Request] = []

    def _logging_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    monkeypatch.setattr(stt, "_transport", httpx.MockTransport(_logging_handler))
    return requests


def _ok(language: object = "english", **extra: object):
    payload = {"text": "I was chased through a forest.", "language": language, **extra}
    return lambda request: httpx.Response(200, json=payload)


def _form_field(body: bytes, name: str) -> bytes | None:
    """The value of multipart field `name`, None if absent."""
    marker = f'name="{name}"\r\n\r\n'.encode()
    if marker not in body:
        return None
    return body.split(marker, 1)[1].split(b"\r\n", 1)[0]


# --- settings ---------------------------------------------------------------


def test_transcription_is_off_by_default():
    s = Settings(_env_file=None, llm_base_url="https://llm.example.test/v1", llm_api_key="k")
    assert s.stt_model == ""
    assert not s.stt_enabled


def test_base_url_and_key_fall_back_to_the_llm_endpoint():
    s = Settings(
        _env_file=None,
        llm_base_url="https://llm.example.test/v1/",
        llm_api_key="llm-key",
        stt_model="m",
    )
    assert (s.stt_base, s.stt_key) == ("https://llm.example.test/v1", "llm-key")
    assert s.stt_enabled

    own = Settings(
        _env_file=None,
        llm_base_url="https://llm.example.test/v1",
        llm_api_key="llm-key",
        stt_base_url="https://stt.example.test/v1",
        stt_api_key="stt-key",
        stt_model="m",
    )
    assert (own.stt_base, own.stt_key) == ("https://stt.example.test/v1", "stt-key")


@pytest.mark.parametrize(
    ("speech", "borrows"),
    [
        ({}, False),  # the LLM endpoint and key
        ({"stt_base_url": "https://LLM.example.test/v1/"}, False),  # the LLM's host
        # A project-scoped path on the LLM's host (Scaleway) keeps its key.
        ({"stt_base_url": "https://llm.example.test/project-1/v1"}, False),
        ({"stt_base_url": _BASE}, True),  # another host, the LLM's key
        ({"stt_base_url": "https://llm.example.test:8001/v1"}, True),  # another port
        ({"stt_base_url": _BASE, "stt_api_key": "own"}, False),
    ],
)
def test_llm_key_sent_to_another_host_is_flagged(speech, borrows):
    """LLM_API_KEY is only reused on LLM_BASE_URL's host: for another host
    with an empty STT_API_KEY transcription stays off (and is warned about)."""
    s = Settings(
        _env_file=None,
        llm_base_url="https://llm.example.test/v1",
        llm_api_key="llm-key",
        stt_model="m",
        **speech,
    )
    assert s.stt_borrows_llm_key is borrows
    assert s.stt_enabled is not borrows
    assert s.stt_key == ("" if borrows else s.stt_api_key.strip() or "llm-key")


@pytest.mark.parametrize(
    "missing",
    [
        {"stt_model": "  "},
        {"stt_base_url": "", "llm_base_url": ""},
        {"stt_api_key": "", "llm_api_key": ""},
    ],
)
def test_enabled_needs_a_model_a_base_url_and_a_key(missing):
    complete = {"stt_base_url": _BASE, "stt_api_key": "k", "stt_model": "m"}
    assert Settings(_env_file=None, **complete).stt_enabled
    assert not Settings(_env_file=None, **{**complete, **missing}).stt_enabled


def test_values_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("STT_BASE_URL", _BASE)
    monkeypatch.setenv("STT_API_KEY", "stt-key")
    monkeypatch.setenv("STT_MODEL", "voxtral-mini-2602")

    s = Settings(_env_file=None)

    assert (s.stt_base, s.stt_key, s.stt_model) == (_BASE, "stt-key", "voxtral-mini-2602")
    assert s.stt_enabled


# --- requests ---------------------------------------------------------------


async def test_posts_multipart_to_the_configured_endpoint(monkeypatch):
    requests = _install_transport(monkeypatch, _ok())

    resp = await stt.transcribe(audio_bytes=b"fake-audio", file_name="dream.mp3")

    assert resp.text == "I was chased through a forest."
    assert resp.language_detected == Language.EN  # "english" mapped to the enum
    (request,) = requests
    assert request.method == "POST"
    assert str(request.url) == f"{_BASE}/audio/transcriptions"
    assert request.headers["Authorization"] == "Bearer stt-key"
    assert request.headers["Content-Type"].startswith("multipart/form-data")
    assert request.extensions["timeout"]["read"] == 120.0
    body = request.read()
    assert _form_field(body, "model") == b"whisper-test"
    assert b'name="file"; filename="dream.mp3"' in body
    assert b"fake-audio" in body
    assert _form_field(body, "language") is None  # not known: the provider detects it


async def test_language_is_sent_when_known(monkeypatch):
    requests = _install_transport(monkeypatch, _ok("de"))

    await stt.transcribe(audio_bytes=b"x", file_name="a.wav", language=Language.DE)

    assert _form_field(requests[0].read(), "language") == b"de"


async def test_llm_endpoint_is_used_without_its_own(monkeypatch):
    monkeypatch.setattr(stt.settings, "stt_base_url", "")
    monkeypatch.setattr(stt.settings, "stt_api_key", "")
    requests = _install_transport(monkeypatch, _ok())

    await stt.transcribe(audio_bytes=b"x", file_name="a.mp3")

    assert str(requests[0].url) == "https://llm.example.test/v1/audio/transcriptions"
    assert requests[0].headers["Authorization"] == "Bearer llm-key"


async def test_trailing_slash_in_the_base_url(monkeypatch):
    monkeypatch.setattr(stt.settings, "stt_base_url", _BASE + "/")
    requests = _install_transport(monkeypatch, _ok())

    await stt.transcribe(audio_bytes=b"x", file_name="a.mp3")

    assert str(requests[0].url) == f"{_BASE}/audio/transcriptions"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"language": "en"}, Language.EN),
        ({"language": "english"}, Language.EN),
        ({"language": "English"}, Language.EN),
        ({"language": "en-US"}, Language.EN),
        ({"language": "de"}, Language.DE),
        ({"language": "german"}, Language.DE),
        ({"language": "deutsch"}, Language.DE),
        ({"language": " DE "}, Language.DE),
        ({"language": "de_DE"}, Language.DE),
        ({"language": "fr"}, None),
        ({"language": None}, None),
        ({"language": 7}, None),
        ({}, None),
        # OpenAI's gpt-transcribe reports a list of detected languages.
        ({"languages": [{"code": "de"}]}, Language.DE),
        ({"languages": []}, None),
    ],
)
async def test_detected_language_is_mapped(monkeypatch, payload, expected):
    _install_transport(
        monkeypatch, lambda request: httpx.Response(200, json={"text": "t", **payload})
    )

    resp = await stt.transcribe(audio_bytes=b"x", file_name="a.wav")

    assert resp.language_detected == expected


# --- failures ---------------------------------------------------------------


@pytest.mark.parametrize("status", [400, 401, 404, 413, 422])
async def test_hard_4xx_is_raised_at_once(monkeypatch, status):
    """Bad key, unknown model, unsupported format: no retry."""
    requests = _install_transport(
        monkeypatch, lambda request: httpx.Response(status, text="model not found")
    )

    with pytest.raises(LlmUnavailableError, match=f"STT {status}"):
        await stt.transcribe(audio_bytes=b"x", file_name="a.mp3")

    assert len(requests) == 1


def _fail(status: int | None):
    def handler(request: httpx.Request) -> httpx.Response:
        if status is None:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(status, text="busy")

    return handler


@pytest.mark.parametrize("status", [429, 500, 503, None])  # None: connection error
async def test_transient_error_is_retried_once_and_recovers(monkeypatch, status):
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return _fail(status)(request) if len(calls) == 1 else _ok()(request)

    requests = _install_transport(monkeypatch, handler)

    resp = await stt.transcribe(audio_bytes=b"x", file_name="a.mp3")

    assert resp.text == "I was chased through a forest."
    assert len(requests) == 2


@pytest.mark.parametrize("status", [429, 503, None])
async def test_exhausted_retries_raise_unavailable(monkeypatch, status):
    requests = _install_transport(monkeypatch, _fail(status))

    with pytest.raises(LlmUnavailableError):
        await stt.transcribe(audio_bytes=b"x", file_name="a.mp3")

    assert len(requests) == stt._ATTEMPTS == 2


async def test_a_success_without_json_is_unavailable(monkeypatch):
    _install_transport(monkeypatch, lambda request: httpx.Response(200, text="<html>"))

    with pytest.raises(LlmUnavailableError, match="no JSON"):
        await stt.transcribe(audio_bytes=b"x", file_name="a.mp3")


@pytest.mark.parametrize("payload", [["x"], "text", 7])
async def test_a_success_without_a_json_object_is_unavailable(monkeypatch, payload):
    """Not an AttributeError: the backend logs that as an unexpected AI error."""
    requests = _install_transport(monkeypatch, lambda request: httpx.Response(200, json=payload))

    with pytest.raises(LlmUnavailableError, match="no JSON object"):
        await stt.transcribe(audio_bytes=b"x", file_name="a.mp3")
    assert len(requests) == 1


@pytest.mark.parametrize(
    "off",
    [{"stt_model": ""}, {"stt_base_url": "", "llm_base_url": ""}],
)
async def test_switched_off_sends_nothing(monkeypatch, off):
    for name, value in off.items():
        monkeypatch.setattr(stt.settings, name, value)
    requests = _install_transport(monkeypatch, _ok())

    assert not stt.settings.stt_enabled
    with pytest.raises(LlmUnavailableError, match="speech input is off"):
        await stt.transcribe(audio_bytes=b"x", file_name="a.mp3")
    assert requests == []


# --- entry point --------------------------------------------------------------


async def test_entry_point_raises_unavailable(monkeypatch):
    """The provider down: the backend's ai_client turns this into AIServiceError."""
    _install_transport(monkeypatch, lambda request: httpx.Response(503, text="down"))

    with pytest.raises(LlmUnavailableError):
        await audio.transcribe(file_name="dream.mp3", audio_bytes=b"fake-audio")


async def test_entry_point_happy_path(monkeypatch):
    _install_transport(
        monkeypatch, lambda request: httpx.Response(200, json={"text": "hello", "language": "en"})
    )

    resp = await audio.transcribe(file_name="dream.mp3", audio_bytes=b"fake-audio")
    body = resp.model_dump(mode="json")
    assert body["text"] == "hello"
    assert body["language_detected"] == "en"


@pytest.mark.parametrize(("file_name", "size", "status"), [("", 1, 400), ("dream.mp3", 11, 413)])
async def test_entry_point_rejects_before_any_provider_call(monkeypatch, file_name, size, status):
    monkeypatch.setattr(audio, "MAX_AUDIO_BYTES", 10)
    requests = _install_transport(monkeypatch, lambda request: httpx.Response(200, json={}))

    with pytest.raises(HTTPException) as exc_info:
        await audio.transcribe(file_name=file_name, audio_bytes=b"x" * size)

    assert exc_info.value.status_code == status
    assert requests == []
