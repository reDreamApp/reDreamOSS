"""TTS: one provider from configuration (TTS_*) + the Supabase Storage cache (ADR 0010).

Fully offline: an httpx.MockTransport goes in via the module's `_transport`
seam and stands in for both the speech endpoint and Supabase Storage. Both
TTS_API_STYLEs are covered: ``openai`` (``voice``, the MP3 as the body) and
``mistral`` (``voice_id``, the MP3 base64-encoded in ``audio_data``).
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import redream_ai.api.audio as audio
import redream_ai.audio.tts as tts
from redream_ai.config import TTS_LANGUAGES, Settings
from redream_ai.llm.base import LlmUnavailableError
from redream_ai.models import Language, RehearsalRequest

_AUDIO = b"ID3-fake-mp3"
_BASE = "https://tts.example.test/v1"
_SPEECH_URL = f"{_BASE}/audio/speech"


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(tts, "_RETRY_DELAY", 0.0)
    s = tts.settings
    monkeypatch.setattr(s, "llm_base_url", "https://llm.example.test/v1")
    monkeypatch.setattr(s, "llm_api_key", "llm-key")
    monkeypatch.setattr(s, "tts_base_url", _BASE)
    monkeypatch.setattr(s, "tts_api_key", "tts-key")
    monkeypatch.setattr(s, "tts_model", "tts-model")
    monkeypatch.setattr(s, "tts_api_style", "openai")
    monkeypatch.setattr(s, "tts_voice", "marin")
    monkeypatch.setattr(s, "tts_voice_en", "")
    monkeypatch.setattr(s, "tts_voice_de", "")
    monkeypatch.setattr(s, "supabase_url", "http://kong:8000")
    monkeypatch.setattr(s, "supabase_public_url", "https://supabase.example.com")
    monkeypatch.setattr(s, "supabase_service_role_key", "srk")
    monkeypatch.setattr(s, "supabase_storage_bucket", "rehearsal-audio")


@pytest.fixture
def mistral_style(monkeypatch):
    s = tts.settings
    monkeypatch.setattr(s, "tts_api_style", "mistral")
    monkeypatch.setattr(s, "tts_model", "voxtral-mini-tts-3-0")
    monkeypatch.setattr(s, "tts_voice", "")
    monkeypatch.setattr(s, "tts_voice_en", "gb_jane_neutral")
    monkeypatch.setattr(s, "tts_voice_de", "voice_481")


def _req(text: str = "You walk home with a warm lantern.", lang: Language = Language.EN):
    return RehearsalRequest(dream_id="7", user_id="u-1", rewritten_dream=text, language=lang)


class _Fake:
    """The speech endpoint (in the configured style) + a dict-backed Storage bucket.

    `speech_status` may be a status code or None for a connection error.
    """

    def __init__(self, *, speech_status: int | None = 200, audio: bytes = _AUDIO) -> None:
        self.objects: dict[str, bytes] = {}
        self.speech_status = speech_status
        self.audio = audio
        self.speech_calls: list[dict[str, object]] = []
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url == _SPEECH_URL:
            assert request.headers["Authorization"] == f"Bearer {tts.settings.tts_key}"
            self.speech_calls.append(json.loads(request.content))
            if self.speech_status is None:
                raise httpx.ConnectError("refused", request=request)
            if self.speech_status != 200:
                return httpx.Response(self.speech_status, text="nope")
            if tts.settings.tts_api_style == "mistral":
                encoded = base64.b64encode(self.audio).decode()
                return httpx.Response(200, json={"audio_data": encoded})
            return httpx.Response(200, content=self.audio, headers={"Content-Type": "audio/mpeg"})
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


def _answer(monkeypatch, response: httpx.Response) -> list[httpx.Request]:
    """The speech endpoint answers `response` to every request."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response

    monkeypatch.setattr(tts, "_transport", httpx.MockTransport(handler))
    return requests


# --- settings ---------------------------------------------------------------

_COMPLETE = {
    "tts_base_url": _BASE,
    "tts_api_key": "k",
    "tts_model": "m",
    "tts_voice": "v",
    "tts_voice_en": "",
    "tts_voice_de": "",
}


def test_speech_output_is_off_by_default():
    fields = Settings.model_fields
    for name in ("tts_base_url", "tts_api_key", "tts_model", "tts_voice"):
        assert fields[name].default == ""
    assert fields["tts_api_style"].default == "openai"
    s = Settings(_env_file=None, llm_base_url="https://llm.example.test/v1", llm_api_key="k")
    assert not s.tts_enabled


def test_every_wire_language_needs_a_voice():
    """A new Language must get its TTS_VOICE_<LANG> and count for tts_enabled."""
    assert set(TTS_LANGUAGES) == {lang.value for lang in Language}


def test_one_voice_serves_every_language_unless_overridden():
    s = Settings(_env_file=None, **_COMPLETE)
    assert (s.tts_voice_for("en"), s.tts_voice_for("de")) == ("v", "v")
    assert s.tts_enabled

    s = Settings(_env_file=None, **{**_COMPLETE, "tts_voice_de": " voice_481 "})
    assert (s.tts_voice_for("en"), s.tts_voice_for("de")) == ("v", "voice_481")

    # Per-language voices alone are enough once each language has one.
    s = Settings(_env_file=None, **{**_COMPLETE, "tts_voice": "", "tts_voice_en": "a"})
    assert not s.tts_enabled  # German has no voice
    s = Settings(
        _env_file=None, **{**_COMPLETE, "tts_voice": "", "tts_voice_en": "a", "tts_voice_de": "b"}
    )
    assert s.tts_enabled


@pytest.mark.parametrize(
    "missing",
    [
        {"tts_model": " "},
        {"tts_voice": ""},
        {"tts_base_url": "", "llm_base_url": ""},
        {"tts_api_key": "", "llm_api_key": ""},
    ],
)
def test_enabled_needs_a_model_a_voice_a_base_url_and_a_key(missing):
    assert not Settings(_env_file=None, **{**_COMPLETE, **missing}).tts_enabled


def test_base_url_and_key_fall_back_to_the_llm_endpoint():
    s = Settings(
        _env_file=None,
        **{**_COMPLETE, "tts_base_url": "", "tts_api_key": ""},
        llm_base_url="https://llm.example.test/v1/",
        llm_api_key="llm-key",
    )
    assert (s.tts_base, s.tts_key) == ("https://llm.example.test/v1", "llm-key")
    assert s.tts_enabled
    assert not s.tts_borrows_llm_key


@pytest.mark.parametrize(
    ("speech", "borrows"),
    [
        ({"tts_base_url": "https://LLM.example.test/v1"}, False),  # the LLM's host
        ({"tts_base_url": _BASE, "tts_api_key": ""}, True),  # another host, the LLM's key
        ({"tts_base_url": _BASE, "tts_api_key": "own"}, False),
    ],
)
def test_llm_key_sent_to_another_host_is_flagged(speech, borrows):
    """LLM_API_KEY is only reused on LLM_BASE_URL's host: for another host
    with an empty TTS_API_KEY speech output stays off (and is warned about)."""
    s = Settings(
        _env_file=None,
        **{**_COMPLETE, "tts_api_key": "", **speech},
        llm_base_url="https://llm.example.test/v1",
        llm_api_key="llm-key",
    )
    assert s.tts_borrows_llm_key is borrows
    assert s.tts_enabled is not borrows


@pytest.mark.parametrize(
    ("raw", "style"), [("mistral", "mistral"), (" OpenAI ", "openai"), ("", "openai")]
)
def test_api_style_is_normalized(monkeypatch, raw, style):
    monkeypatch.setenv("TTS_API_STYLE", raw)
    assert Settings(_env_file=None).tts_api_style == style


def test_unknown_api_style_is_rejected(monkeypatch):
    monkeypatch.setenv("TTS_API_STYLE", "elevenlabs")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


# --- speech requests ----------------------------------------------------------


async def test_openai_style_body_and_raw_response(monkeypatch):
    requests = _answer(
        monkeypatch, httpx.Response(200, content=_AUDIO, headers={"Content-Type": "audio/mpeg"})
    )

    audio_bytes = await tts.synthesize("A warm lantern.", Language.DE)

    assert audio_bytes == _AUDIO
    (request,) = requests
    assert str(request.url) == _SPEECH_URL
    assert request.headers["Authorization"] == "Bearer tts-key"
    # Per attempt; the backend's rehearsal budget is sized for two of these.
    assert request.extensions["timeout"]["read"] == 60.0
    assert json.loads(request.content) == {
        "model": "tts-model",
        "input": "A warm lantern.",
        "voice": "marin",
        "response_format": "mp3",
    }


async def test_mistral_style_body_and_base64_response(monkeypatch, mistral_style):
    requests = _answer(
        monkeypatch, httpx.Response(200, json={"audio_data": base64.b64encode(_AUDIO).decode()})
    )

    audio_bytes = await tts.synthesize("Eine warme Laterne.", Language.DE)

    assert audio_bytes == _AUDIO
    assert json.loads(requests[0].content) == {
        "model": "voxtral-mini-tts-3-0",
        "input": "Eine warme Laterne.",
        "voice_id": "voice_481",
        "response_format": "mp3",
    }


async def test_llm_endpoint_is_used_without_its_own(monkeypatch):
    monkeypatch.setattr(tts.settings, "tts_base_url", "")
    monkeypatch.setattr(tts.settings, "tts_api_key", "")
    requests = _answer(monkeypatch, httpx.Response(200, content=_AUDIO))

    await tts.synthesize("x", Language.EN)

    assert str(requests[0].url) == "https://llm.example.test/v1/audio/speech"
    assert requests[0].headers["Authorization"] == "Bearer llm-key"


@pytest.mark.parametrize(
    ("style", "response", "match"),
    [
        ("openai", httpx.Response(200, json={"audio_data": "AAAA"}), "JSON, not audio"),
        ("openai", httpx.Response(200, content=b""), "no audio"),
        ("mistral", httpx.Response(200, json={"error": "none"}), "no audio_data"),
        ("mistral", httpx.Response(200, json={"audio_data": "A"}), "unreadable"),  # not base64
        ("mistral", httpx.Response(200, content=b"\xff\xfb raw mp3"), "unreadable"),
    ],
)
async def test_a_success_without_audio_is_unavailable(monkeypatch, style, response, match):
    """Also what a wrong TTS_API_STYLE for the provider looks like."""
    monkeypatch.setattr(tts.settings, "tts_api_style", style)
    requests = _answer(monkeypatch, response)

    with pytest.raises(LlmUnavailableError, match=match):
        await tts.synthesize("x", Language.EN)
    assert len(requests) == 1


@pytest.mark.parametrize("status", [400, 401, 404, 422])
async def test_hard_tts_error_is_not_retried(monkeypatch, status):
    fake = _install(monkeypatch, _Fake(speech_status=status))
    with pytest.raises(LlmUnavailableError, match=f"TTS {status}"):
        await tts.generate_rehearsal(_req())
    assert len(fake.speech_calls) == 1
    assert fake.objects == {}


@pytest.mark.parametrize("status", [429, 500, 503, None])  # None: connection error
async def test_transient_tts_error_is_retried(monkeypatch, status):
    fake = _install(monkeypatch, _Fake(speech_status=status))
    with pytest.raises(LlmUnavailableError):
        await tts.generate_rehearsal(_req())
    assert len(fake.speech_calls) == tts._ATTEMPTS == 2
    assert fake.objects == {}


@pytest.mark.parametrize("style", ["openai", "mistral"])
@pytest.mark.parametrize("status", [429, 503, None])
async def test_transient_tts_error_recovers_on_retry(monkeypatch, style, status):
    monkeypatch.setattr(tts.settings, "tts_api_style", style)
    fake = _Fake(speech_status=status)

    def recover(request):
        if len(fake.speech_calls) == 1:
            fake.speech_status = 200
        return fake(request)

    monkeypatch.setattr(tts, "_transport", httpx.MockTransport(recover))
    response = await tts.generate_rehearsal(_req())
    assert response.cache_hit is False
    assert len(fake.speech_calls) == 2
    assert list(fake.objects.values()) == [_AUDIO]


# --- rehearsal: synthesis + Storage -------------------------------------------


@pytest.mark.parametrize("style", ["openai", "mistral"])
async def test_generates_uploads_and_signs(monkeypatch, style):
    monkeypatch.setattr(tts.settings, "tts_api_style", style)
    fake = _install(monkeypatch, _Fake())

    resp = await tts.generate_rehearsal(_req())

    assert resp.cache_hit is False
    assert resp.voice_id == "marin"
    assert resp.script == "You walk home with a warm lantern."
    assert resp.duration_estimate == "0:03"
    (path,) = fake.objects
    assert path.startswith("users/u-1/7/v2-en-") and path.endswith(".mp3")
    assert fake.objects[path] == _AUDIO
    voice_field = "voice_id" if style == "mistral" else "voice"
    assert fake.speech_calls == [
        {
            "model": "tts-model",
            "input": "You walk home with a warm lantern.",
            voice_field: "marin",
            "response_format": "mp3",
        }
    ]
    # Signed URL points at the public host, not the internal compose address.
    assert resp.audio_url == (
        f"https://supabase.example.com/storage/v1/object/sign/rehearsal-audio/{path}?token=t"
    )


async def test_stored_audio_gets_a_seek_header(monkeypatch):
    """The provider's MP3 may carry no length header; players misreport it without one."""
    # MPEG-2 Layer III frames, 80 kbit/s, 22.05 kHz mono.
    audio_bytes = ((0xFFF390C0).to_bytes(4, "big") + bytes(257)) * 4
    fake = _install(monkeypatch, _Fake(audio=audio_bytes))

    await tts.generate_rehearsal(_req())

    (stored,) = fake.objects.values()
    assert b"Xing" in stored
    assert stored.endswith(audio_bytes)


async def test_unchanged_dream_reuses_the_stored_file(monkeypatch):
    fake = _install(monkeypatch, _Fake())
    first = await tts.generate_rehearsal(_req())
    second = await tts.generate_rehearsal(_req())

    assert second.cache_hit is True
    assert second.audio_url == first.audio_url
    assert len(fake.speech_calls) == 1


def test_cache_key_covers_model_voice_language_and_script():
    base = {"model": "m", "voice": "v"}
    path = tts.object_path(_req(), "script", **base)
    assert path == tts.object_path(_req(), "script", **base)  # stable
    assert path.startswith("users/u-1/7/v2-en-")  # the prefix the URL refresh expects
    variants = [
        tts.object_path(_req(), "script", model="m2", voice="v"),
        tts.object_path(_req(), "script", model="m", voice="v2"),
        tts.object_path(_req(lang=Language.DE), "script", **base),
        tts.object_path(_req(), "script 2", **base),
    ]
    assert len({path, *variants}) == 5


async def test_edited_dream_or_language_gets_a_new_file(monkeypatch, mistral_style):
    fake = _install(monkeypatch, _Fake())
    responses = [
        await tts.generate_rehearsal(_req()),
        await tts.generate_rehearsal(_req("You fly over the forest.")),
        await tts.generate_rehearsal(_req(lang=Language.DE)),
    ]

    assert len(fake.objects) == 3
    assert len(fake.speech_calls) == 3
    voices = ["gb_jane_neutral", "gb_jane_neutral", "voice_481"]
    assert [c["voice_id"] for c in fake.speech_calls] == voices
    # The response names the voice the language resolved to, overrides included.
    assert [r.voice_id for r in responses] == voices


@pytest.mark.parametrize(
    ("setting", "before", "after"),
    [("tts_voice_de", "gb_jane_neutral", "voice_481"), ("tts_model", "tts-a", "tts-b")],
)
async def test_new_voice_or_model_gets_a_new_file(monkeypatch, setting, before, after):
    """Dreams rendered in the old voice or model get new audio in the new one."""
    fake = _install(monkeypatch, _Fake())
    monkeypatch.setattr(tts.settings, setting, before)
    await tts.generate_rehearsal(_req(lang=Language.DE))
    monkeypatch.setattr(tts.settings, setting, after)
    second = await tts.generate_rehearsal(_req(lang=Language.DE))

    assert second.cache_hit is False
    assert len(fake.objects) == 2
    assert [c[setting.removeprefix("tts_").removesuffix("_de")] for c in fake.speech_calls] == [
        before,
        after,
    ]


async def test_missing_storage_config_is_unavailable(monkeypatch):
    monkeypatch.setattr(tts.settings, "supabase_service_role_key", "")
    with pytest.raises(LlmUnavailableError, match="storage not configured"):
        await tts.generate_rehearsal(_req())


@pytest.mark.parametrize(
    "off",
    [
        {"tts_model": ""},
        {"tts_voice": "", "tts_voice_en": "gb_jane_neutral"},  # no German voice
        {"tts_api_key": "", "llm_api_key": ""},
    ],
)
async def test_switched_off_sends_nothing(monkeypatch, off):
    for name, value in off.items():
        monkeypatch.setattr(tts.settings, name, value)
    fake = _install(monkeypatch, _Fake())

    assert not tts.settings.tts_enabled
    with pytest.raises(LlmUnavailableError, match="speech output is off"):
        await tts.generate_rehearsal(_req())
    with pytest.raises(LlmUnavailableError, match="speech output is off"):
        await tts.synthesize("x", Language.EN)
    assert fake.requests == []


# --- entry point --------------------------------------------------------------


async def test_entry_point_raises_unavailable(monkeypatch):
    """The backend's ai_client turns this into AIServiceError."""
    _install(monkeypatch, _Fake(speech_status=400))
    with pytest.raises(LlmUnavailableError):
        await audio.rehearsal_generate(_req("x"))


async def test_entry_point_rejects_empty_dream(monkeypatch):
    fake = _install(monkeypatch, _Fake())
    with pytest.raises(HTTPException) as exc_info:
        await audio.rehearsal_generate(_req("  "))
    assert exc_info.value.status_code == 400
    assert fake.requests == []


@pytest.mark.parametrize(("words", "expected"), [(1, "0:01"), (24, "0:10"), (360, "2:30")])
def test_duration_estimate(words: int, expected: str):
    assert tts.estimate_duration(" ".join(["word"] * words)) == expected
