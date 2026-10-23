"""InProcessAiClient — the in-process boundary to redream_ai.

Pins what the routes rely on: the same dict payloads, return shapes and SSE
frame strings the HTTP client had; every AI-side failure (and a timeout)
surfaces as AIServiceError while CancelledError passes; a consumer that stops
early closes the AI stream; no AI route is reachable over HTTP. The AI side is
mocked at its seams (ported core, entry points, STT/TTS), so nothing reaches a
provider.

redream_ai is imported inside the tests, never at collection: tests/api's
conftest (and tests/ai's, in a full run) pins the core's dummy LLM endpoint
before the core's first import.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import subprocess
import sys
import types
from collections.abc import Callable
from typing import Any

import httpx
import openai
import pydantic_core
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from redream_api.db.models.chat import Chat
from redream_api.db.models.chat_message import ChatMessage
from redream_api.main import app
from redream_api.services import ai_client as ai_client_mod
from redream_api.services.ai_client import AIServiceError, InProcessAiClient, get_ai_client

from .conftest import start_chat

_CONTENT_1 = 'data: {"content": "You turn ", "stages": ["rewriting"], "language": "en"}'
_CONTENT_2 = 'data: {"content": "and face it.", "stages": ["rewriting"], "language": "en"}'
_REHEARSAL_USER = "0f8b6c1e-2a3d-4e5f-8a9b-0c1d2e3f4a5b"
_REHEARSAL = {
    "dream_id": "7",
    "user_id": _REHEARSAL_USER,
    "rewritten_dream": "A lantern.",
    "language": "en",
}
_REHEARSAL_KEY = f"users/{_REHEARSAL_USER}/7/en-{'a' * 24}.mp3"
_VOICE_ON = {
    "stt_base_url": "http://speech.invalid/v1",
    "stt_api_key": "stt-key",
    "stt_model": "stt-test",
    "tts_base_url": "http://speech.invalid/v1",
    "tts_api_key": "tts-key",
    "tts_model": "tts-test",
    "tts_api_style": "openai",
    "tts_voice": "voice-test",
    "tts_voice_en": "",
    "tts_voice_de": "",
}


def _chat_payload() -> dict[str, Any]:
    """The payload exactly as the chat route builds it (`_ai_chat_payload`)."""
    from redream_api.api.v1.chats.routes import _ai_chat_payload

    chat = Chat(id=7, language="en", nightmare_summary="Chased through a forest.")
    history = [
        ChatMessage(chat_id=7, role="user", content="I was chased.", stage="recording"),
        ChatMessage(chat_id=7, role="assistant", content="Rewrite it?", stage="recording"),
        ChatMessage(chat_id=7, role="user", content="I turn and face it", stage="rewriting"),
    ]
    return _ai_chat_payload(chat, history)


@pytest.fixture
def ai(monkeypatch) -> types.SimpleNamespace:
    """The redream_ai modules the client calls into, with the core's agents
    stubbed ready (no LLM behind them: every test mocks below the adapter)
    and speech switched on (the switch-off tests turn it off again)."""
    import redream_ai.api.audio as audio
    import redream_ai.api.chat as chat
    import redream_ai.api.safety as safety
    import redream_ai.audio.stt as stt
    import redream_ai.audio.tts as tts
    from redream_ai.config import settings
    from redream_ai.core import irt_app

    monkeypatch.setattr(irt_app, "routing_agent", object())
    monkeypatch.setattr(irt_app, "response_agent", object())
    for name, value in _VOICE_ON.items():
        monkeypatch.setattr(settings, name, value)
    return types.SimpleNamespace(
        audio=audio, chat=chat, safety=safety, stt=stt, tts=tts, irt_app=irt_app
    )


@pytest.fixture
def safety_llm(ai, monkeypatch) -> Any:
    """A FakeLlmClient as the crisis classifier's endpoint."""
    from redream_ai.llm.fake_client import FakeLlmClient

    llm = FakeLlmClient()
    monkeypatch.setattr(ai.safety, "build_safety_llm_client", lambda: llm)
    return llm


@pytest.fixture
def ai_client(safety_llm) -> InProcessAiClient:
    return InProcessAiClient()


_LLM_REQUIRED = "LLM_BASE_URL, LLM_API_KEY, LLM_MODEL and LLM_SMALL_MODEL"


# ---------------------------------------------------------------------------
# Payloads in, wire shapes out.
# ---------------------------------------------------------------------------


async def test_chat_validates_the_route_payload_and_returns_wire_json(ai, ai_client, monkeypatch):
    from redream_ai.core.models import ChatResponse as PortedResponse

    captured: dict[str, Any] = {}

    async def fake(chat_input, conversation, safety_enabled=True):
        captured["message"] = chat_input.message
        captured["history"] = [(m.role, m.content) for m in conversation.messages]
        captured["session_id"] = conversation.session_id
        conversation.stages.append("rewriting")
        return PortedResponse(
            session_id="7",
            stage="rewriting",
            response="You turn and face it.",
            stages=conversation.stages,
            language="en",
            usage={"input": 11, "output": 22, "total": 33},
            safety={"is_safe": True},
        )

    monkeypatch.setattr(ai.irt_app, "process_chat_message", fake)

    resp = await ai_client.chat(_chat_payload())

    assert resp == {
        "stage": "rewriting",
        "response": "You turn and face it.",
        "structured": None,
        "usage": {"input_tokens": 11, "output_tokens": 22},
        "prompt_version": ai.chat.PROMPT_VERSION,
        "safety": {"is_safe": True},
        "nightmare_summary": "Chased through a forest.",
    }
    assert captured == {
        "message": "I turn and face it",
        "history": [("user", "I was chased."), ("assistant", "Rewrite it?")],
        "session_id": "7",
    }


async def test_chat_rejects_an_invalid_payload_without_calling_the_core(ai, ai_client, monkeypatch):
    async def never(*args, **kwargs):
        raise AssertionError("the core must not run on an invalid payload")

    monkeypatch.setattr(ai.irt_app, "process_chat_message", never)

    with pytest.raises(AIServiceError, match="chat failed: invalid ChatRequest") as exc_info:
        await ai_client.chat({"messages": "my secret dream", "language": "xx"})
    assert "my secret dream" not in str(exc_info.value)  # no input values in the message


async def test_chat_stream_yields_the_http_clients_frames(ai, ai_client, monkeypatch):
    """Stripped `data: <payload>` strings, one per SSE event — what the HTTP client
    yielded and what the route (and FakeAiClient) parse."""

    async def fake(chat_input, conversation, safety_enabled=True):
        conversation.stages.append("rewriting")
        yield _CONTENT_1 + "\n\n"
        yield _CONTENT_2 + "\n\n"
        yield 'data: {"usage": {"input": 3, "output": 4, "total": 7}}\n\n'
        yield 'data: {"safety": {"is_safe": true}}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(ai.irt_app, "process_chat_message_stream", fake)

    frames = [frame async for frame in ai_client.chat_stream(_chat_payload())]

    assert frames[:3] == [_CONTENT_1, _CONTENT_2, 'data: {"safety": {"is_safe": true}}']
    assert json.loads(frames[3].removeprefix("data: "))["final"] == {
        "stage": "rewriting",
        "response": "You turn and face it.",
        "structured": None,
        "usage": {"input_tokens": 3, "output_tokens": 4},
        "prompt_version": ai.chat.PROMPT_VERSION,
        "safety": {"is_safe": True},
        "nightmare_summary": "Chased through a forest.",
    }
    assert frames[4] == "data: [DONE]"
    assert len(frames) == 5


async def test_chat_stream_rejection_raises_before_any_frame(ai, ai_client):
    stream = ai_client.chat_stream({"messages": [], "language": "en"})
    with pytest.raises(AIServiceError, match="chat_stream failed: 400 messages must not be empty"):
        await anext(stream)


async def test_chat_stream_splits_multi_frame_chunks(ai, ai_client, monkeypatch):
    """An item carrying several frames (or blank padding) still comes out as one
    stripped frame per SSE event, as the HTTP client's line buffer did."""

    async def frames():
        yield "data: a\n\ndata: b\n\n"
        yield "\n\n  \n\ndata: c\n\n\n\n"

    monkeypatch.setattr(ai.chat, "chat_stream", lambda req: frames())

    out = [frame async for frame in ai_client.chat_stream(_chat_payload())]

    assert out == ["data: a", "data: b", "data: c"]


async def test_safety_classify_returns_wire_json(ai_client, safety_llm):
    from redream_ai.config import settings
    from redream_ai.prompts.safety import PROMPT_VERSION

    safety_llm.enqueue(json.dumps({"risk": "high", "categories": ["self_harm"], "confidence": 0.9}))

    resp = await ai_client.safety_classify(
        {"text": "He hurt me.", "language": "en", "stage": "recording", "context": "And then?"}
    )

    assert resp == {
        "risk": "high",
        "categories": ["self_harm"],
        "confidence": 0.9,
        "prompt_version": PROMPT_VERSION,
    }
    assert [c["model"] for c in safety_llm.calls] == [settings.safety_model]


async def test_an_unparseable_crisis_answer_fails_without_fallback(ai_client, safety_llm):
    """Fail-closed: no JSON object in the answer, no verdict — the route
    answers 502 and the turn is blocked."""
    safety_llm.enqueue("I am not sure how to classify this.")

    with pytest.raises(AIServiceError) as exc_info:
        await ai_client.safety_classify({"text": "He hurt me.", "language": "en"})

    assert str(exc_info.value) == "safety_classify failed: internal error (ValueError)"
    assert len(safety_llm.calls) == 1


@pytest.mark.parametrize("missing", ["llm_base_url", "llm_api_key", "llm_model", "llm_small_model"])
async def test_safety_classify_unconfigured_is_an_ai_service_error(ai, monkeypatch, missing):
    """Any of the four unset gives no client (never a constructor error at
    startup) and an AIServiceError naming them."""
    from redream_ai.config import settings

    monkeypatch.setattr(settings, missing, "")
    assert ai.safety.build_safety_llm_client() is None

    with pytest.raises(AIServiceError) as exc_info:
        await InProcessAiClient().safety_classify({"text": "x", "language": "en"})

    assert str(exc_info.value) == (
        f"safety_classify failed: crisis classifier not configured (set {_LLM_REQUIRED})"
    )


async def test_chat_unconfigured_names_the_llm_settings(ai, ai_client, monkeypatch):
    monkeypatch.setattr(ai.irt_app, "routing_agent", None)

    with pytest.raises(AIServiceError) as exc_info:
        await ai_client.chat(_chat_payload())

    assert str(exc_info.value) == (
        f"chat failed: 503 IRT agents not configured (set {_LLM_REQUIRED})"
    )


def test_crisis_client_is_built_from_the_llm_settings(ai, monkeypatch):
    """One client on LLM_BASE_URL with the crisis check's own, shorter timeout."""
    from redream_ai.config import settings
    from redream_ai.llm.openai_compatible import OpenAICompatibleLlmClient

    monkeypatch.setattr(settings, "llm_base_url", "https://llm.example.test/v1")
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    monkeypatch.setattr(settings, "llm_timeout_seconds", 60.0)
    monkeypatch.setattr(settings, "llm_safety_timeout_seconds", 7.0)

    llm = ai.safety.build_safety_llm_client()

    assert isinstance(llm, OpenAICompatibleLlmClient)
    assert str(llm._client.base_url) == "https://llm.example.test/v1/"
    assert llm._client.api_key == "sk-test"
    assert llm._client.timeout == 7.0
    assert llm._client.max_retries == 1


async def test_transcribe_returns_wire_json(ai, ai_client, monkeypatch):
    from redream_ai.models import Language, TranscribeResponse

    seen: dict[str, Any] = {}

    async def fake(*, audio_bytes, file_name):
        seen.update(audio_bytes=audio_bytes, file_name=file_name)
        return TranscribeResponse(text="Ich wurde gejagt.", language_detected=Language.DE)

    monkeypatch.setattr(ai.stt, "transcribe", fake)

    resp = await ai_client.transcribe(file_name="dream.m4a", audio_bytes=b"audio")

    assert resp == {"text": "Ich wurde gejagt.", "language_detected": "de"}
    assert seen == {"audio_bytes": b"audio", "file_name": "dream.m4a"}


async def test_rehearsal_generate_validates_and_returns_wire_json(ai, ai_client, monkeypatch):
    from redream_ai.models import Language, RehearsalRequest, RehearsalResponse

    seen: list[RehearsalRequest] = []

    async def fake(req):
        seen.append(req)
        return RehearsalResponse(
            audio_key=_REHEARSAL_KEY,
            script="A lantern.",
            duration_estimate="0:01",
            voice_id="gb_jane_neutral",
        )

    monkeypatch.setattr(ai.tts, "generate_rehearsal", fake)

    resp = await ai_client.rehearsal_generate(_REHEARSAL)

    assert resp == {
        "audio_key": _REHEARSAL_KEY,
        "script": "A lantern.",
        "duration_estimate": "0:01",
        "voice_id": "gb_jane_neutral",
        "cache_hit": False,
    }
    assert seen == [
        RehearsalRequest(
            dream_id="7",
            user_id=_REHEARSAL_USER,
            rewritten_dream="A lantern.",
            language=Language.EN,
        )
    ]


# ---------------------------------------------------------------------------
# Speech switched off: no STT/TTS call at all.
# ---------------------------------------------------------------------------


def test_speech_switches_follow_the_settings(ai, ai_client, monkeypatch):
    from redream_ai.config import settings

    assert ai_client.stt_enabled and ai_client.tts_enabled
    monkeypatch.setattr(settings, "stt_model", "")
    monkeypatch.setattr(settings, "tts_voice", "")
    monkeypatch.setattr(settings, "tts_voice_en", "voice-en")  # German has none
    assert not ai_client.stt_enabled
    assert not ai_client.tts_enabled


@pytest.mark.parametrize(
    ("method", "setting", "reason"),
    [
        ("transcribe", "stt_model", "speech input is off"),
        ("rehearsal_generate", "tts_model", "speech output is off"),
    ],
)
async def test_switched_off_speech_is_never_called(
    ai, ai_client, monkeypatch, method, setting, reason
):
    from redream_ai.config import settings

    async def never(*args, **kwargs):
        raise AssertionError("STT/TTS must not run while switched off")

    monkeypatch.setattr(settings, setting, "")
    module, name = _seams(ai)[method]
    monkeypatch.setattr(module, name, never)
    monkeypatch.setattr(ai.stt, "transcribe", never)
    monkeypatch.setattr(ai.tts, "generate_rehearsal", never)

    with pytest.raises(AIServiceError) as exc_info:
        await _calls(ai_client)[method]()

    assert str(exc_info.value) == f"{method} failed: {reason}"


def test_readiness_reports_switched_off_speech(ai, safety_llm, monkeypatch, caplog):
    """Off (no model) is an info; a model without a voice is a warning."""
    from redream_ai.config import settings

    monkeypatch.setattr(settings, "stt_model", "")
    monkeypatch.setattr(settings, "tts_voice", "")

    with caplog.at_level(logging.INFO, logger=ai_client_mod.__name__):
        InProcessAiClient().log_readiness()

    speech = [
        (r.levelno, r.getMessage().split(" — ")[0])
        for r in caplog.records
        if r.name == ai_client_mod.__name__ and r.getMessage().startswith("speech")
    ]
    assert speech == [
        (logging.INFO, "speech input off"),
        (logging.WARNING, "speech output off"),
    ]


def test_readiness_warns_when_speech_sends_the_llm_key_elsewhere(
    ai, safety_llm, monkeypatch, caplog
):
    """An empty STT_/TTS_API_KEY with a speech host other than LLM_BASE_URL's
    would get LLM_API_KEY: that half of speech stays off, with a warning (no key logged)."""
    from redream_ai.config import settings

    monkeypatch.setattr(settings, "llm_base_url", "https://llm.example.test/v1")
    monkeypatch.setattr(settings, "llm_api_key", "llm-secret")
    monkeypatch.setattr(settings, "stt_api_key", "")
    monkeypatch.setattr(settings, "tts_api_key", "")

    with caplog.at_level(logging.INFO, logger=ai_client_mod.__name__):
        InProcessAiClient().log_readiness()

    speech = [
        (r.levelno, r.getMessage())
        for r in caplog.records
        if r.name == ai_client_mod.__name__ and r.getMessage().startswith("speech")
    ]
    assert [(level, message.split(":")[0]) for level, message in speech] == [
        (logging.WARNING, "speech input off"),
        (logging.WARNING, "speech output off"),
    ]
    assert "STT_BASE_URL is on another host" in speech[0][1]
    assert "TTS_BASE_URL is on another host" in speech[1][1]
    assert not any("llm-secret" in message for _, message in speech)
    assert not InProcessAiClient().stt_enabled
    assert not InProcessAiClient().tts_enabled


# ---------------------------------------------------------------------------
# Failures: any Exception -> AIServiceError; CancelledError passes.
# ---------------------------------------------------------------------------


def _validation_error() -> ValidationError:
    from redream_ai.models import SafetyResponse

    try:
        SafetyResponse.model_validate({"risk": "maybe", "confidence": 7, "prompt_version": "v"})
    except ValidationError as exc:
        return exc
    raise AssertionError("expected a ValidationError")


def _unavailable() -> Exception:
    from redream_ai.llm.base import LlmUnavailableError

    return LlmUnavailableError("LLM endpoint 503")


_REQUEST = httpx.Request("POST", "http://llm.invalid/v1/chat/completions")

# (exception factory, what the AIServiceError says)
_FAILURES: list[tuple[Callable[[], Exception], str]] = [
    (lambda: HTTPException(503, "IRT agents not configured"), "503 IRT agents not configured"),
    (_unavailable, "LLM provider temporarily unavailable: LLM endpoint 503"),
    (lambda: ValueError("unmapped stage"), "internal error (ValueError)"),
    (lambda: TimeoutError(), "internal error (TimeoutError)"),  # not our deadline
    (lambda: openai.APIConnectionError(request=_REQUEST), "internal error (APIConnectionError)"),
    (lambda: httpx.ConnectError("refused", request=_REQUEST), "internal error (ConnectError)"),
    (lambda: KeyError("risk"), "internal error (KeyError)"),
    (lambda: RuntimeError("boom"), "internal error (RuntimeError)"),
    (_validation_error, "invalid SafetyResponse (2 validation errors)"),
]


def _calls(ai_client: InProcessAiClient) -> dict[str, Callable[[], Any]]:
    async def chat_stream() -> None:
        async for _ in ai_client.chat_stream(_chat_payload()):
            pass

    return {
        "chat": lambda: ai_client.chat(_chat_payload()),
        "chat_stream": chat_stream,
        "safety_classify": lambda: ai_client.safety_classify({"text": "x", "language": "en"}),
        "transcribe": lambda: ai_client.transcribe(file_name="a.mp3", audio_bytes=b"x"),
        "rehearsal_generate": lambda: ai_client.rehearsal_generate(_REHEARSAL),
    }


def _seams(ai: types.SimpleNamespace) -> dict[str, tuple[object, str]]:
    """The redream_ai entry point each client method calls."""
    return {
        "chat": (ai.chat, "chat"),
        "chat_stream": (ai.chat, "chat_stream"),
        "safety_classify": (ai.safety, "safety_classify"),
        "transcribe": (ai.audio, "transcribe"),
        "rehearsal_generate": (ai.audio, "rehearsal_generate"),
    }


@pytest.mark.parametrize(("make_exc", "reason"), _FAILURES)
async def test_every_exception_type_becomes_ai_service_error(
    ai, ai_client, monkeypatch, make_exc, reason
):
    exc = make_exc()

    async def boom(*args, **kwargs):
        raise exc

    monkeypatch.setattr(ai.chat, "chat", boom)

    with pytest.raises(AIServiceError) as exc_info:
        await ai_client.chat(_chat_payload())

    assert str(exc_info.value) == f"chat failed: {reason}"
    assert exc_info.value.__cause__ is exc


@pytest.mark.parametrize(
    "method", ["chat", "chat_stream", "safety_classify", "transcribe", "rehearsal_generate"]
)
async def test_every_method_maps_failures(ai, ai_client, monkeypatch, method):
    from redream_ai.llm.base import LlmUnavailableError

    def boom(*args, **kwargs):  # chat_stream is a plain function returning the stream
        raise LlmUnavailableError("down")

    async def aboom(*args, **kwargs):
        boom()

    module, name = _seams(ai)[method]
    monkeypatch.setattr(module, name, boom if method == "chat_stream" else aboom)

    with pytest.raises(AIServiceError, match=f"^{method} failed: LLM provider"):
        await _calls(ai_client)[method]()


class _Unserializable:
    """A response holding a non-JSON value: its JSON dump raises."""

    def model_dump(self, **kwargs: Any) -> Any:
        return pydantic_core.to_jsonable_python(object())


@pytest.mark.parametrize("method", ["chat", "safety_classify", "transcribe", "rehearsal_generate"])
async def test_a_response_that_fails_to_serialize_is_an_ai_service_error(
    ai, ai_client, monkeypatch, method
):
    """The old AI service answered 500 here; the route must still see
    AIServiceError (502 + its rollback), not a raw serialization error."""

    async def unserializable(*args, **kwargs):
        return _Unserializable()

    module, name = _seams(ai)[method]
    monkeypatch.setattr(module, name, unserializable)

    with pytest.raises(AIServiceError) as exc_info:
        await _calls(ai_client)[method]()

    assert str(exc_info.value) == f"{method} failed: internal error (PydanticSerializationError)"


async def test_mid_stream_failure_follows_the_frames_seen_so_far(ai, ai_client, monkeypatch):
    """Like the HTTP client: frames already received come first, then the
    error (the route turns it into an error frame and rolls back)."""

    async def frames():
        yield _CONTENT_1 + "\n\n"
        raise ConnectionResetError("provider dropped the stream")

    monkeypatch.setattr(ai.chat, "chat_stream", lambda req: frames())

    seen: list[str] = []
    with pytest.raises(AIServiceError, match=r"chat_stream failed: internal error"):
        async for frame in ai_client.chat_stream(_chat_payload()):
            seen.append(frame)
    assert seen == [_CONTENT_1]


@pytest.mark.parametrize("method", ["chat", "chat_stream"])
async def test_cancellation_is_not_mapped(ai, ai_client, monkeypatch, method):
    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError

    async def cancelled_stream():
        yield _CONTENT_1 + "\n\n"
        raise asyncio.CancelledError

    if method == "chat":
        monkeypatch.setattr(ai.chat, "chat", cancelled)
    else:
        monkeypatch.setattr(ai.chat, "chat_stream", lambda req: cancelled_stream())

    with pytest.raises(asyncio.CancelledError):
        await _calls(ai_client)[method]()


@pytest.mark.parametrize(
    "method", ["chat", "chat_stream", "safety_classify", "transcribe", "rehearsal_generate"]
)
async def test_timeout_becomes_ai_service_error(ai, ai_client, monkeypatch, method):
    for budget in ("_CHAT_TIMEOUT", "_TRANSCRIBE_TIMEOUT", "_REHEARSAL_TIMEOUT"):
        monkeypatch.setattr(ai_client_mod, budget, 0.01)

    async def slow(*args, **kwargs):
        await asyncio.sleep(5)

    async def slow_stream():
        yield _CONTENT_1 + "\n\n"
        await asyncio.sleep(5)  # silence mid-stream: the per-frame deadline

    module, name = _seams(ai)[method]
    monkeypatch.setattr(
        module, name, (lambda req: slow_stream()) if method == "chat_stream" else slow
    )

    with pytest.raises(AIServiceError, match=f"^{method} timed out after"):
        await _calls(ai_client)[method]()


async def test_chat_stream_deadline_is_per_frame_not_per_stream(ai, ai_client, monkeypatch):
    """A healthy answer may stream for longer than the budget in total: only a
    gap between two frames longer than it times out (the HTTP read timeout)."""
    monkeypatch.setattr(ai_client_mod, "_CHAT_TIMEOUT", 0.2)

    async def steady():
        for i in range(6):
            await asyncio.sleep(0.06)  # 0.36 s in total, every gap well inside 0.2 s
            yield "data: " + json.dumps({"content": str(i)}) + "\n\n"

    monkeypatch.setattr(ai.chat, "chat_stream", lambda req: steady())

    frames = [frame async for frame in ai_client.chat_stream(_chat_payload())]

    assert [json.loads(f.removeprefix("data: "))["content"] for f in frames] == [
        str(i) for i in range(6)
    ]


# The budget each method runs under.
_BUDGETS = {
    "chat": "_CHAT_TIMEOUT",
    "chat_stream": "_CHAT_TIMEOUT",
    "safety_classify": "_CHAT_TIMEOUT",
    "transcribe": "_TRANSCRIBE_TIMEOUT",
    "rehearsal_generate": "_REHEARSAL_TIMEOUT",
}


class _Dumps:
    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        return {"ok": True}


@pytest.mark.parametrize("method", list(_BUDGETS))
async def test_each_method_runs_under_its_own_budget(ai, ai_client, monkeypatch, method):
    """Only the method's own budget is generous: a call slower than the other
    budgets still succeeds (rehearsal gets 150 s, not the chat's 90 s)."""
    for budget in set(_BUDGETS.values()):
        monkeypatch.setattr(ai_client_mod, budget, 1.0 if budget == _BUDGETS[method] else 0.01)

    async def slow(*args, **kwargs):
        await asyncio.sleep(0.05)
        return _Dumps()

    async def slow_stream():
        await asyncio.sleep(0.05)
        yield _CONTENT_1 + "\n\n"

    module, name = _seams(ai)[method]
    monkeypatch.setattr(
        module, name, (lambda req: slow_stream()) if method == "chat_stream" else slow
    )

    await _calls(ai_client)[method]()  # no AIServiceError


def test_timeouts_match_the_http_boundary():
    """AI_REQUEST_TIMEOUT was 90 s for every call; rehearsal had 150 s."""
    assert ai_client_mod._CHAT_TIMEOUT == 90.0
    assert ai_client_mod._TRANSCRIBE_TIMEOUT == 90.0
    assert ai_client_mod._REHEARSAL_TIMEOUT == 150.0


# ---------------------------------------------------------------------------
# Disconnects close the AI stream deterministically.
# ---------------------------------------------------------------------------


async def test_closing_the_consumer_early_closes_the_ai_stream(ai, ai_client, monkeypatch):
    streams: list[Any] = []
    closed: list[bool] = []

    async def frames(conversation):
        try:
            conversation.stages.append("rewriting")
            for i in range(3):
                yield "data: " + json.dumps({"content": str(i)}) + "\n\n"
        finally:
            closed.append(True)

    def fake(chat_input, conversation, safety_enabled=True):
        stream = frames(conversation)
        streams.append(stream)  # a live reference: only an explicit aclose() closes it
        return stream

    monkeypatch.setattr(ai.irt_app, "process_chat_message_stream", fake)

    stream = ai_client.chat_stream(_chat_payload())
    assert json.loads((await anext(stream)).removeprefix("data: ")) == {"content": "0"}
    assert closed == []
    await stream.aclose()

    assert closed == [True]


# ---------------------------------------------------------------------------
# Wiring: startup, no HTTP surface, lazy import.
# ---------------------------------------------------------------------------


async def test_lifespan_builds_the_client_once_and_logs_readiness(
    ai, safety_llm, monkeypatch, caplog
):
    from redream_ai.config import settings

    monkeypatch.setattr(ai_client_mod, "_default_client", None)
    monkeypatch.setattr(ai.irt_app, "safety_critic", None)

    with caplog.at_level(logging.INFO, logger=ai_client_mod.__name__):
        async with app.router.lifespan_context(app):
            built = ai_client_mod._default_client
            assert isinstance(built, InProcessAiClient)
            assert get_ai_client() is built

    messages = [r.getMessage() for r in caplog.records if r.name == ai_client_mod.__name__]
    assert messages == [
        "IRT pipeline ready (agents=ok, safety_critic=disabled (fail-open))",
        f"crisis classifier ready (model={settings.safety_model})",
        "speech input ready (model=stt-test)",
        "speech output ready (model=tts-test, style=openai)",
    ]
    assert app.title == "reDreamOSS server"


def test_readiness_warns_when_unconfigured(ai, monkeypatch, caplog):
    monkeypatch.setattr(ai.irt_app, "routing_agent", None)
    monkeypatch.setattr(ai.safety, "build_safety_llm_client", lambda: None)

    with caplog.at_level(logging.INFO, logger=ai_client_mod.__name__):
        InProcessAiClient().log_readiness()

    warnings = [
        r.getMessage()
        for r in caplog.records
        if r.name == ai_client_mod.__name__ and r.levelno == logging.WARNING
    ]
    assert [w.split(" — ")[0] for w in warnings] == [
        "IRT agents UNCONFIGURED",
        "crisis classifier UNCONFIGURED",
    ]
    assert all(f"set {_LLM_REQUIRED};" in w for w in warnings), warnings


_DOCS_ROUTES = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def test_no_ai_route_is_reachable_over_http():
    paths = [getattr(route, "path", "") for route in app.routes]

    assert not [p for p in paths if p.startswith(("/chat", "/safety", "/audio", "/rehearsal"))]
    assert "/health" not in paths  # the AI service's own probe; ours is /api/v1/health
    assert all(p.startswith("/api/v1/") or p in _DOCS_ROUTES for p in paths), paths
    endpoint_modules = {
        getattr(getattr(route, "endpoint", None), "__module__", "") for route in app.routes
    }
    assert not [m for m in endpoint_modules if m.startswith("redream_ai")]
    assert importlib.util.find_spec("redream_ai.main") is None  # the AI app is gone


def test_importing_the_app_does_not_import_redream_ai():
    """The backend imports redream_ai on first use only (lifespan or request)."""
    code = (
        "import sys, redream_api.main; "
        "loaded = sorted(m for m in sys.modules if m.split('.')[0] == 'redream_ai'); "
        "print(loaded); sys.exit(1 if loaded else 0)"
    )
    env = {**os.environ, "DATABASE_URL": "sqlite+aiosqlite:///:memory:"}
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr


# ---------------------------------------------------------------------------
# End to end: the real client behind the streaming chat route.
# ---------------------------------------------------------------------------


async def test_stream_route_over_the_in_process_client(
    client, ai, ai_client, safety_llm, monkeypatch
):
    from redream_ai.config import settings

    safety_llm.enqueue(json.dumps({"risk": "none", "categories": [], "confidence": 0.9}))

    async def fake(chat_input, conversation, safety_enabled=True):
        conversation.stages.append("rewriting")
        yield _CONTENT_1 + "\n\n"
        yield _CONTENT_2 + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(ai.irt_app, "process_chat_message_stream", fake)
    app.dependency_overrides[get_ai_client] = lambda: ai_client

    chat_id = await start_chat(client)
    r = await client.post(f"/api/v1/chats/{chat_id}/message/stream", json={"content": "hi there"})

    bodies = [part.removeprefix("data: ") for part in r.text.split("\n\n") if part]
    assert bodies[-1] == "[DONE]"
    frames = [json.loads(b) for b in bodies[:-1]]
    assert [next(iter(f)) for f in frames] == ["user_message", "content", "content", "done"]
    assert frames[3]["done"]["ai_message"]["content"] == "You turn and face it."
    msgs = (await client.get(f"/api/v1/chats/{chat_id}/messages")).json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    # The route's crisis check went through the real client too.
    assert [c["model"] for c in safety_llm.calls] == [settings.safety_model]


async def test_stream_route_timeout_is_an_error_frame_and_rollback(
    client, ai, ai_client, safety_llm, monkeypatch
):
    """A silent AI stream mid-turn: error frame + [DONE], user message rolled
    back — the same path an HTTP read timeout took."""
    from redream_ai.config import settings

    safety_llm.enqueue(json.dumps({"risk": "none", "categories": [], "confidence": 0.9}))
    monkeypatch.setattr(ai_client_mod, "_CHAT_TIMEOUT", 0.05)

    async def fake(chat_input, conversation, safety_enabled=True):
        conversation.stages.append("rewriting")
        yield _CONTENT_1 + "\n\n"
        await asyncio.sleep(5)

    monkeypatch.setattr(ai.irt_app, "process_chat_message_stream", fake)
    app.dependency_overrides[get_ai_client] = lambda: ai_client

    chat_id = await start_chat(client)
    r = await client.post(f"/api/v1/chats/{chat_id}/message/stream", json={"content": "hi there"})

    bodies = [part.removeprefix("data: ") for part in r.text.split("\n\n") if part]
    assert bodies[-1] == "[DONE]"
    error = json.loads(bodies[-2])["error"]
    assert error.startswith("chat_stream timed out after")
    assert (await client.get(f"/api/v1/chats/{chat_id}/messages")).json() == []
    assert [c["model"] for c in safety_llm.calls] == [settings.safety_model]
