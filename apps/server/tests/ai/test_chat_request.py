"""Reasoning models and parameters an endpoint rejects (redream_ai.llm.chat_request).

Pins which models count as reasoning models, the parameters each kind of model
gets (a reasoning model's token limit raised by LLM_REASONING_EXTRA_TOKENS),
the one warning when a reasoning model's answer comes back empty because the
limit ran out (naming the limit as it went out, or that none was sent), the
answer read out of Mistral's lists of chunks, and the adaptation to an
endpoint that answers 400 or 422 to a parameter it does not take: adjusted and
sent again once per parameter, at most three times per call, max_tokens and
max_completion_tokens renamed into each other but never back, remembered for
the endpoint and model once the endpoint got past it, one warning — and every
other 400 or 422 raised as it is, with nothing remembered: a validator's
errors read one by one, the same error again after an adjustment raised.
Fully offline: an httpx.MockTransport stands in for the endpoint, so the SDK
raises its real errors.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

import httpx
import openai
import pytest

from redream_ai import summary_structured
from redream_ai.config import settings
from redream_ai.core import agent as core_agent
from redream_ai.llm import chat_request
from redream_ai.llm.chat_request import (
    answer_text,
    build_chat_request,
    create_chat_completion,
    is_reasoning_model,
)
from redream_ai.llm.openai_compatible import OpenAICompatibleLlmClient
from redream_ai.models import Language, SafetyRisk
from redream_ai.safety import classify

_BASE_URL = "https://llm.example.test/v1"
_USER_TEXT = "I was drowning in the lake behind my parents' house"
_MESSAGES = [{"role": "user", "content": _USER_TEXT}]
# Every parameter the adaptation knows, on one request.
_FULL: dict[str, Any] = {
    "model": "m",
    "messages": _MESSAGES,
    "temperature": 0.2,
    "top_p": 0.9,
    "max_tokens": 64,
    "response_format": {"type": "json_object"},
    "reasoning_effort": "low",
}
_VERDICT = json.dumps({"risk": "none", "categories": [], "confidence": 0.9})
# LLM_REASONING_EXTRA_TOKENS' default.
_EXTRA = 2048


@pytest.fixture(autouse=True)
def _defaults(monkeypatch) -> None:
    """Production defaults. (No adjustment is left over from another test:
    conftest clears them.)"""
    monkeypatch.setattr(settings, "llm_reasoning_models", "")
    monkeypatch.setattr(settings, "llm_reasoning_effort", "low")
    monkeypatch.setattr(settings, "llm_reasoning_extra_tokens", _EXTRA)


def _unsupported(name: str) -> dict[str, Any]:
    """OpenAI's answer to a parameter the model does not take."""
    return {
        "error": {
            "message": f"Unsupported parameter: '{name}' is not supported with this model.",
            "type": "invalid_request_error",
            "param": name,
            "code": "unsupported_parameter",
        }
    }


def _extra_forbidden(name: str) -> dict[str, Any]:
    """Mistral's answer (422) to a field its request schema does not have, as
    public reports quote it from api.mistral.ai."""
    return {
        "object": "error",
        "message": {
            "detail": [
                {
                    "type": "extra_forbidden",
                    "loc": ["body", name],
                    "msg": "Extra inputs are not permitted",
                    "input": 0,
                }
            ]
        },
        "type": "invalid_request_error",
        "param": None,
        "code": None,
        "raw_status_code": 422,
    }


def _pydantic_extra_forbidden(name: str) -> dict[str, Any]:
    """A FastAPI/pydantic endpoint's own 422 for an extra field."""
    return {
        "detail": [
            {
                "type": "extra_forbidden",
                "loc": ["body", name],
                "msg": "Extra inputs are not permitted",
                "input": 0,
                "url": "https://errors.pydantic.dev/2.9/v/extra_forbidden",
            }
        ]
    }


# The ways an endpoint says it does not take a parameter: (status, body).
_REJECTIONS = {
    "400": (400, _unsupported),
    "422": (422, _unsupported),
    "422-mistral": (422, _extra_forbidden),
    "422-pydantic": (422, _pydantic_extra_forbidden),
}
_SDK_ERRORS = {400: openai.BadRequestError, 422: openai.UnprocessableEntityError}


def _completion(content: object, finish_reason: str = "stop") -> dict[str, Any]:
    return {
        "id": "x",
        "object": "chat.completion",
        "created": 0,
        "model": "m",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
    }


def _sse(
    contents: list[object], finish_reason: str = "stop", usage: dict[str, int] | None = None
) -> httpx.Response:
    head = {"id": "x", "object": "chat.completion.chunk", "created": 0, "model": "m"}
    frames: list[dict[str, Any]] = [
        {"choices": [{"index": 0, "delta": {"content": c}, "finish_reason": None}]}
        for c in contents
    ]
    frames.append({"choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}]})
    if usage is not None:
        # stream_options include_usage: OpenAI's last frame, after the finish
        # chunk, has the usage and no choices.
        frames.append({"choices": [], "usage": usage})
    body = "".join(f"data: {json.dumps({**head, **f})}\n\n" for f in frames) + "data: [DONE]\n\n"
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


class _Provider:
    """An endpoint that records every request body and answers `status` to the
    first of `rejects` a request carries (`error(name)` is the body: a dict as
    JSON, a str as plain text). It answers everything else with `answer`, or
    a stream of `streamed` (then `usage` in a frame of its own when given),
    finished with `finish_reason`."""

    def __init__(
        self,
        *rejects: str,
        error: Callable[[str], dict[str, Any] | str] = _unsupported,
        status: int = 400,
        answer: object = _VERDICT,
        streamed: tuple[object, ...] = ("Hello ", "there."),
        finish_reason: str = "stop",
        usage: dict[str, int] | None = None,
    ) -> None:
        self.rejects = rejects
        self.error = error
        self.status = status
        self.answer = answer
        self.streamed = streamed
        self.finish_reason = finish_reason
        self.usage = usage
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        for name in self.rejects:
            if name in body:
                error = self.error(name)
                if isinstance(error, str):
                    return httpx.Response(self.status, text=error)
                return httpx.Response(self.status, json=error)
        if body.get("stream"):
            return _sse(list(self.streamed), self.finish_reason, self.usage)
        return httpx.Response(200, json=_completion(self.answer, self.finish_reason))


def _sdk(
    provider: Callable[[httpx.Request], httpx.Response], base_url: str = _BASE_URL
) -> openai.AsyncOpenAI:
    return openai.AsyncOpenAI(
        base_url=base_url,
        api_key="sk-test",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(provider)),
    )


def _crisis_client(provider: _Provider) -> OpenAICompatibleLlmClient:
    return OpenAICompatibleLlmClient(
        _BASE_URL,
        "sk-test",
        timeout=5.0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(provider)),
    )


def _agent(model: str, provider: _Provider) -> core_agent.Agent:
    llm = core_agent.Agent(model, "system prompt", temperature=0.1)
    llm.client = _sdk(provider)
    return llm


def _adjusted(request: dict[str, Any], *names: str) -> dict[str, Any]:
    """`request` as it goes out once `names` are adjusted (a token limit
    renamed unless both names are among them)."""
    out = {k: v for k, v in request.items() if k not in names}
    for name, other in _RENAMED.items():
        if name in names and name in request and other not in names:
            out[other] = request[name]
    return out


_RENAMED = {"max_tokens": "max_completion_tokens", "max_completion_tokens": "max_tokens"}


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == chat_request.__name__]


# --- which models are reasoning models ------------------------------------------


@pytest.mark.parametrize(
    "model",
    [
        "o1",
        "o1-mini",
        "o3",
        "o3-mini",
        "o4-mini",
        "gpt-5",
        "gpt-5-mini",
        "gpt-5.1",
        "openai/gpt-5-nano",
        "openai/o1",
        "openai/o1-mini",
        "openai/o3",
        "openai/o4-mini",
    ],
)
def test_openai_reasoning_families_are_known_by_name(model):
    assert is_reasoning_model(model)


@pytest.mark.parametrize(
    "model",
    ["gpt-4o", "gpt-4.1-mini", "openai/gpt-4o", "mistral-large-2512", "magistral-medium-2509"],
)
def test_other_models_are_not_reasoning_models(model):
    assert not is_reasoning_model(model)


def test_listed_models_are_reasoning_models(monkeypatch):
    monkeypatch.setattr(settings, "llm_reasoning_models", "magistral-medium-2509, qwq-32b")

    assert is_reasoning_model("magistral-medium-2509")
    assert is_reasoning_model("qwq-32b")
    # A listed name matches as a whole, not as a prefix.
    assert not is_reasoning_model("magistral-medium")
    assert not is_reasoning_model("qwq-32b-preview")
    assert not is_reasoning_model("mistral-large-2512")


# --- the parameters each model gets -----------------------------------------------


def test_a_regular_model_gets_the_parameters_as_given():
    assert build_chat_request("m", _MESSAGES, temperature=0.2) == {
        "model": "m",
        "messages": _MESSAGES,
        "temperature": 0.2,
    }
    assert build_chat_request(
        "m",
        _MESSAGES,
        temperature=0.0,
        max_tokens=64,
        response_format={"type": "json_object"},
        reasoning_effort="high",
    ) == {
        "model": "m",
        "messages": _MESSAGES,
        "temperature": 0.0,
        "max_tokens": 64,
        "response_format": {"type": "json_object"},
        "reasoning_effort": "high",
    }


@pytest.mark.parametrize("listed", [False, True], ids=["by-name", "listed"])
def test_a_reasoning_model_gets_no_temperature_and_max_completion_tokens(monkeypatch, listed):
    model = "thinker" if listed else "o4-mini"
    if listed:
        monkeypatch.setattr(settings, "llm_reasoning_models", "thinker")

    request = build_chat_request(
        model, _MESSAGES, temperature=0.2, max_tokens=64, response_format={"type": "json_object"}
    )

    assert request == {
        "model": model,
        "messages": _MESSAGES,
        "max_completion_tokens": 64 + _EXTRA,
        "response_format": {"type": "json_object"},
        "reasoning_effort": "low",
    }


@pytest.mark.parametrize(("setting", "sent"), [("low", "low"), ("high", "high"), ("", None)])
def test_reasoning_effort_follows_the_setting(monkeypatch, setting, sent):
    monkeypatch.setattr(settings, "llm_reasoning_effort", setting)

    request = build_chat_request("gpt-5-mini", _MESSAGES, temperature=0.2)

    assert request.get("reasoning_effort") == sent
    assert "max_completion_tokens" not in request  # no limit asked, none sent
    # An explicit effort wins over the setting.
    explicit = build_chat_request("gpt-5-mini", _MESSAGES, temperature=0.2, reasoning_effort="x")
    assert explicit["reasoning_effort"] == "x"


@pytest.mark.parametrize("extra", [0, 1, 4096])
def test_the_extra_tokens_go_to_reasoning_models_only(monkeypatch, extra):
    monkeypatch.setattr(settings, "llm_reasoning_extra_tokens", extra)

    reasoning = build_chat_request("o3-mini", _MESSAGES, temperature=0.2, max_tokens=64)
    regular = build_chat_request("m", _MESSAGES, temperature=0.2, max_tokens=64)

    assert reasoning["max_completion_tokens"] == 64 + extra  # 0: the limit as asked
    assert regular == {"model": "m", "messages": _MESSAGES, "temperature": 0.2, "max_tokens": 64}
    # No limit asked, none sent.
    assert "max_completion_tokens" not in build_chat_request("o3-mini", _MESSAGES, temperature=0)


async def test_agent_requests_for_a_reasoning_model():
    provider = _Provider()
    llm = _agent("o4-mini", provider)

    await llm.generate("hello", response_format={"type": "json_object"})
    [item async for item in llm.generate_stream("hello")]

    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "hello"},
    ]
    shaped = {
        "model": "o4-mini",
        "messages": messages,
        "max_completion_tokens": 1024 + _EXTRA,
        "reasoning_effort": "low",
    }
    assert provider.bodies == [
        {**shaped, "response_format": {"type": "json_object"}},
        {**shaped, "stream": True},
    ]


async def test_crisis_check_request_for_a_reasoning_model():
    provider = _Provider()

    result = await classify(
        llm=_crisis_client(provider), model="gpt-5-mini", text=_USER_TEXT, language=Language.EN
    )

    assert result.risk is SafetyRisk.NONE
    (body,) = provider.bodies
    assert body == {
        "model": "gpt-5-mini",
        "messages": body["messages"],
        "response_format": {"type": "json_object"},
        "reasoning_effort": "low",
    }


async def test_crisis_client_stream_request_for_a_reasoning_model():
    provider = _Provider()
    llm = _crisis_client(provider)

    chunks = [
        c async for c in llm.chat_stream(model="gpt-5-mini", messages=_MESSAGES, max_tokens=64)
    ]

    assert "".join(c["text"] for c in chunks) == "Hello there."
    assert provider.bodies == [
        {
            "model": "gpt-5-mini",
            "messages": _MESSAGES,
            "max_completion_tokens": 64 + _EXTRA,
            "reasoning_effort": "low",
            "stream": True,
            "stream_options": {"include_usage": True},
        }
    ]


@pytest.mark.parametrize("model", ["m", "gpt-5-mini"])
async def test_crisis_client_sends_an_explicit_reasoning_effort(model):
    provider = _Provider()
    llm = _crisis_client(provider)

    await llm.chat(model=model, messages=_MESSAGES, reasoning_effort="high")
    [c async for c in llm.chat_stream(model=model, messages=_MESSAGES, reasoning_effort="high")]

    assert [body["reasoning_effort"] for body in provider.bodies] == ["high", "high"]


# --- a reasoning model that runs out of tokens -------------------------------------


def _request(model: str) -> dict[str, Any]:
    return build_chat_request(model, _MESSAGES, temperature=0.2, max_tokens=64)


@pytest.mark.parametrize("answer", ["", None, " \n"])
async def test_an_empty_answer_cut_off_by_the_limit_is_logged_once_per_model(answer, caplog):
    provider = _Provider(answer=answer, finish_reason="length")
    client = _sdk(provider)

    with caplog.at_level(logging.WARNING, logger=chat_request.__name__):
        for model in ("o4-mini", "o4-mini", "gpt-5-mini", "o4-mini"):
            await create_chat_completion(client, _request(model))

    warnings = _warnings(caplog)
    assert [w.split()[1] for w in warnings] == ["model=o4-mini", "model=gpt-5-mini"]
    for warning in warnings:
        assert "finish_reason=length" in warning
        assert f"LLM_REASONING_EXTRA_TOKENS higher (now {_EXTRA})" in warning
    assert _USER_TEXT not in caplog.text


@pytest.mark.parametrize(
    ("model", "answer", "finish_reason"),
    [
        ("m", "", "length"),  # not a reasoning model
        ("o4-mini", "Half an ans", "length"),  # an answer, cut short
        ("o4-mini", "", "stop"),  # empty, not for want of tokens
        ("o4-mini", "", "content_filter"),
    ],
)
async def test_only_an_empty_reasoning_answer_cut_off_by_the_limit_is_logged(
    model, answer, finish_reason, caplog
):
    provider = _Provider(answer=answer, finish_reason=finish_reason)

    with caplog.at_level(logging.WARNING, logger=chat_request.__name__):
        await create_chat_completion(_sdk(provider), _request(model))

    assert _warnings(caplog) == []


async def test_every_way_a_request_is_sent_logs_running_out(caplog):
    empty = _Provider(answer=None, streamed=(), finish_reason="length")
    # A stream cut off after some text has an answer.
    partial = _Provider(streamed=("Half an ans",), finish_reason="length")

    with caplog.at_level(logging.WARNING, logger=chat_request.__name__):
        for _ in range(2):
            assert (await _agent("o4-mini", empty).generate(_USER_TEXT))[0] == ""
            items = [i async for i in _agent("o3-mini", empty).generate_stream(_USER_TEXT)]
            assert [text for text, _, _ in items] == [""]
            content, _ = await _crisis_client(empty).chat(model="gpt-5-mini", messages=_MESSAGES)
            assert content == ""
            stream = _crisis_client(empty).chat_stream(model="gpt-5-nano", messages=_MESSAGES)
            chunks = [c async for c in stream]
            assert chunks[-1]["finish_reason"] == "length"
            [i async for i in _agent("o1-mini", partial).generate_stream(_USER_TEXT)]
            [c async for c in _crisis_client(partial).chat_stream(model="o1", messages=_MESSAGES)]

    warnings = _warnings(caplog)
    assert [w.split()[1] for w in warnings] == [
        "model=o4-mini",
        "model=o3-mini",
        "model=gpt-5-mini",
        "model=gpt-5-nano",
    ]
    # The agents send the core's limit plus the headroom; the crisis client
    # was given none here, so none went out.
    for warning in warnings[:2]:
        assert f"used up max_completion_tokens={1024 + _EXTRA};" in warning
    for warning in warnings[2:]:
        assert "with no token limit sent" in warning
    assert _USER_TEXT not in caplog.text


_STREAM_USAGE = {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12}
# A Mistral reasoning model's thinking (reasoning_effort high): a chunk of its own.
_THINKING = {"type": "thinking", "thinking": [{"type": "text", "text": "The temperature..."}]}


@pytest.mark.parametrize("usage", [None, _STREAM_USAGE], ids=["no-usage", "usage-frame"])
@pytest.mark.parametrize(
    "streamed",
    [(), (" \n",), (" ", "\n"), ([_THINKING],)],
    ids=["nothing", "whitespace", "whitespace-chunks", "thinking-only"],
)
async def test_both_stream_readers_log_an_empty_streamed_answer(streamed, usage, caplog):
    # With include_usage the last frame comes after the finish chunk and has no
    # choices: the finish reason seen before it still counts.
    provider = _Provider(streamed=streamed, finish_reason="length", usage=usage)

    with caplog.at_level(logging.WARNING, logger=chat_request.__name__):
        items = [i async for i in _agent("o3-mini", provider).generate_stream(_USER_TEXT)]
        stream = _crisis_client(provider).chat_stream(
            model="o4-mini", messages=_MESSAGES, max_tokens=64
        )
        chunks = [c async for c in stream]

    assert [w.split()[1] for w in _warnings(caplog)] == ["model=o3-mini", "model=o4-mini"]
    # Whitespace is passed on as it came; thinking never is.
    assert "".join(text for text, _, _ in items) == "".join(s for s in streamed if s != [_THINKING])
    reported = (5, 7, 12) if usage else (0, 0, 0)
    assert items[-1][1] == dict(zip(("input", "output", "total"), reported, strict=True))
    assert [c["finish_reason"] for c in chunks if "finish_reason" in c] == ["length"]
    if usage:
        assert (chunks[-1]["input_tokens"], chunks[-1]["output_tokens"]) == (5, 7)


@pytest.mark.parametrize("streamed", [False, True])
async def test_after_a_fallback_the_warning_names_the_fallback_model(streamed, caplog):
    bodies: list[dict[str, Any]] = []

    def endpoint(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if body["model"] == "m-retired":
            return httpx.Response(404, json={"error": {"message": "No such model: m-retired"}})
        if body.get("stream"):
            return _sse([], "length")
        return httpx.Response(200, json=_completion("", "length"))

    llm = core_agent.Agent("m-retired", "system prompt", fallback_model="o4-mini")
    llm.client = llm.fallback_client = _sdk(endpoint)

    with caplog.at_level(logging.WARNING, logger=chat_request.__name__):
        if streamed:
            [item async for item in llm.generate_stream(_USER_TEXT)]
        else:
            await llm.generate(_USER_TEXT)

    assert [b["model"] for b in bodies] == ["m-retired", "o4-mini"]
    (warning,) = _warnings(caplog)
    assert warning.startswith("llm.reasoning_out_of_tokens model=o4-mini — ")


async def test_with_no_limit_sent_the_warning_does_not_point_at_the_extra_tokens(caplog):
    # The crisis check sends no token limit, so no headroom is added and
    # raising LLM_REASONING_EXTRA_TOKENS cannot help.
    provider = _Provider(answer="", streamed=(), finish_reason="length")
    llm = _crisis_client(provider)

    with caplog.at_level(logging.WARNING, logger=chat_request.__name__):
        for _ in range(2):
            await llm.chat(model="gpt-5-mini", messages=_MESSAGES)
            [c async for c in llm.chat_stream(model="gpt-5-mini", messages=_MESSAGES)]
        # It does not use up the model's one warning about the limit.
        await llm.chat(model="gpt-5-mini", messages=_MESSAGES, max_tokens=64)
        await llm.chat(model="gpt-5-mini", messages=_MESSAGES, max_tokens=64)

    no_limit, limited = _warnings(caplog)
    assert "finish_reason=length with no token limit sent" in no_limit
    assert "LLM_REASONING_EXTRA_TOKENS only raises a limit that is sent" in no_limit
    assert "set LLM_REASONING_EXTRA_TOKENS higher" not in no_limit
    assert f"used up max_completion_tokens={64 + _EXTRA}; set LLM_REASONING_EXTRA_TOKENS" in limited


async def test_the_warning_names_the_limit_as_it_went_out(monkeypatch, caplog):
    monkeypatch.setattr(settings, "llm_reasoning_models", "magistral-medium-2509")
    # Mistral: max_completion_tokens is sent as max_tokens.
    renamed = _Provider(
        "max_completion_tokens",
        status=422,
        error=_extra_forbidden,
        answer="",
        finish_reason="length",
    )
    # Takes neither name: the limit is left out.
    neither = _Provider("max_tokens", "max_completion_tokens", answer="", finish_reason="length")

    with caplog.at_level(logging.WARNING, logger=chat_request.__name__):
        await _agent("magistral-medium-2509", renamed).generate(_USER_TEXT)
        await create_chat_completion(_sdk(neither), _request("o4-mini"))

    sent_as_max_tokens, left_out = [w for w in _warnings(caplog) if "out_of_tokens" in w]
    assert "model=magistral-medium-2509 " in sent_as_max_tokens
    assert f"used up max_tokens={1024 + _EXTRA}; set LLM_REASONING_EXTRA_TOKENS" in (
        sent_as_max_tokens
    )
    assert "model=o4-mini — empty answer, finish_reason=length with no token limit" in left_out


# --- content as a list of chunks (Mistral's reasoning models) -----------------------


def test_answer_text():
    assert answer_text("Hello ") == "Hello "
    assert answer_text(None) == ""
    assert answer_text(42) == ""
    assert answer_text([_THINKING, {"type": "text", "text": "Hello "}, {"text": "there."}]) == (
        "Hello there."
    )
    assert answer_text([_THINKING]) == ""
    assert answer_text([{"type": "image_url", "text": "x"}, "stray", {"text": 3}]) == ""


async def test_every_reader_takes_the_text_of_a_list_of_chunks(caplog):
    # Thinking, then the answer: complete, or streamed as Mistral streams it
    # (thinking, then the thinking closed and the first text, then strings).
    provider = _Provider(
        answer=[_THINKING, {"type": "text", "text": _VERDICT}],
        streamed=(
            [_THINKING],
            [{**_THINKING, "closed": True}, {"type": "text", "text": "Hello "}],
            "there.",
        ),
        finish_reason="length",
    )
    llm = _agent("o4-mini", provider)
    crisis = _crisis_client(provider)

    with caplog.at_level(logging.WARNING, logger=chat_request.__name__):
        assert (await llm.generate(_USER_TEXT))[0] == _VERDICT
        items = [item async for item in llm.generate_stream(_USER_TEXT)]
        assert (await crisis.chat(model="o4-mini", messages=_MESSAGES))[0] == _VERDICT
        chunks = [c async for c in crisis.chat_stream(model="o4-mini", messages=_MESSAGES)]
        result = await classify(llm=crisis, model="o4-mini", text=_USER_TEXT, language=Language.EN)

    assert [text for text, _, _ in items] == ["Hello ", "there.", ""]
    assert [c["text"] for c in chunks] == ["", "Hello ", "there.", ""]
    assert result.risk is SafetyRisk.NONE
    # Every answer had text, though cut short: nothing to log.
    assert _warnings(caplog) == []


# --- a parameter the endpoint rejects -----------------------------------------------


@pytest.mark.parametrize(("status", "error"), _REJECTIONS.values(), ids=_REJECTIONS.keys())
@pytest.mark.parametrize(
    "parameter", ["temperature", "top_p", "max_tokens", "response_format", "reasoning_effort"]
)
async def test_a_rejected_parameter_is_adjusted_retried_and_remembered(
    parameter, status, error, caplog
):
    provider = _Provider(parameter, status=status, error=error)
    client = _sdk(provider)
    request = dict(_FULL)

    with caplog.at_level(logging.WARNING, logger=chat_request.__name__):
        await create_chat_completion(client, request)
        await create_chat_completion(client, request)

    adjusted = _adjusted(_FULL, parameter)
    # Rejected, sent again adjusted; the next call goes out adjusted at once.
    assert provider.bodies == [_FULL, adjusted, adjusted]
    assert request == _FULL  # the caller's request is never changed
    (warning,) = _warnings(caplog)
    assert "model=m" in warning
    assert f"parameter={parameter}" in warning
    assert _USER_TEXT not in caplog.text


async def test_the_warning_carries_no_text_from_the_error(caplog):
    # A validator of the whole body: its error echoes the request, user text
    # included.
    error = {
        "object": "error",
        "message": {
            "detail": [
                {
                    "type": "value_error",
                    "loc": ["body"],
                    "msg": "Value error, temperature is not supported for this model",
                    "input": {"model": "m", "messages": _MESSAGES, "temperature": 0.2},
                }
            ]
        },
        "type": "invalid_request_error",
        "param": None,
        "code": None,
    }
    provider = _Provider("temperature", error=lambda _name: error)

    with caplog.at_level(logging.WARNING, logger=chat_request.__name__):
        await create_chat_completion(_sdk(provider), _FULL)

    assert provider.bodies == [_FULL, _adjusted(_FULL, "temperature")]
    (warning,) = _warnings(caplog)
    assert "parameter=temperature" in warning
    assert _USER_TEXT not in caplog.text


@pytest.mark.parametrize(
    ("parameter", "error"),
    [
        (
            "temperature",
            {
                "error": {
                    "message": "Unsupported value: 'temperature' does not support 0.2 with "
                    "this model. Only the default (1) value is supported.",
                    "type": "invalid_request_error",
                    "param": "temperature",
                    "code": "unsupported_value",
                }
            },
        ),
        (
            "max_tokens",
            {
                "error": {
                    "message": "Unsupported parameter: 'max_tokens' is not supported with this "
                    "model. Use 'max_completion_tokens' instead.",
                    "type": "invalid_request_error",
                    "param": "max_tokens",
                    "code": "unsupported_parameter",
                }
            },
        ),
        (
            "top_p",
            {"error": {"message": "Unrecognized request argument supplied: top_p", "param": None}},
        ),
        (
            "reasoning_effort",
            {
                "object": "error",
                "message": {
                    "detail": [
                        {
                            "type": "extra_forbidden",
                            "loc": ["body", "reasoning_effort"],
                            "msg": "Extra inputs are not permitted",
                            "input": "low",
                        }
                    ]
                },
                "type": "invalid_request_error",
                "param": None,
                "code": None,
            },
        ),
        # pydantic's error type alone says it, whatever the wording.
        (
            "top_p",
            {
                "detail": [
                    {
                        "type": "extra_forbidden",
                        "loc": ["body", "top_p"],
                        "msg": "Zusätzliche Eingaben sind nicht erlaubt",
                        "input": 0.9,
                    }
                ]
            },
        ),
        ("reasoning_effort", {"error": {"message": "`reasoning_effort` is not supported"}}),
        ("response_format", {"error": {"message": "Unknown parameter: 'response_format'."}}),
        ("temperature", {"error": {"message": "Temperature is not allowed for this model"}}),
        ("response_format", "response_format is not supported by this model"),
        # Names two the request carries: the first named is the one rejected.
        (
            "reasoning_effort",
            {
                "error": {
                    "message": "Unsupported parameter: 'reasoning_effort' (temperature is fine)."
                }
            },
        ),
    ],
    ids=[
        "openai-value",
        "openai-max-tokens",
        "unrecognized",
        "extra-forbidden",
        "extra-forbidden-type-only",
        "backticks",
        "unknown",
        "capitalized",
        "plain-text",
        "names-two",
    ],
)
async def test_how_endpoints_word_a_rejected_parameter(parameter, error):
    provider = _Provider(parameter, error=lambda _name: error)

    await create_chat_completion(_sdk(provider), _FULL)

    assert provider.bodies == [_FULL, _adjusted(_FULL, parameter)]


_OTHER_ERRORS = {
    # Says "not allowed", names none of the parameters.
    "content-policy": {
        "error": {
            "message": "Your request was rejected as a result of our safety system. Your prompt "
            "may contain text that is not allowed by our safety system.",
            "type": "invalid_request_error",
            "param": None,
            "code": "content_policy_violation",
        }
    },
    # A pydantic validator's 422s that are not about a parameter not taken,
    # some naming one of them (Mistral's shape, or FastAPI's bare one).
    "pydantic-missing": {
        "detail": [
            {
                "type": "missing",
                "loc": ["body", "messages"],
                "msg": "Field required",
                "input": {"model": "m", "temperature": 0.2},
            }
        ]
    },
    "pydantic-literal": {
        "object": "error",
        "message": {
            "detail": [
                {
                    "type": "literal_error",
                    "loc": ["body", "reasoning_effort"],
                    "msg": "Input should be 'none', 'minimal', 'low', 'medium', 'high' or 'xhigh'",
                    "input": "max",
                }
            ]
        },
        "type": "invalid_request_error",
        "param": None,
        "code": None,
        "raw_status_code": 422,
    },
    "pydantic-not-a-number": {
        "detail": [
            {
                "type": "float_parsing",
                "loc": ["body", "temperature"],
                "msg": "Input should be a valid number, unable to parse string as a number",
                "input": "warm",
            }
        ]
    },
    "pydantic-out-of-range": {
        "detail": [
            {
                "type": "less_than_equal",
                "loc": ["body", "top_p"],
                "msg": "Input should be less than or equal to 1",
                "input": 1.5,
                "ctx": {"le": 1.0},
            }
        ]
    },
    # A field never adapted; the parameter's name only in the echoed input.
    "pydantic-extra-other": {
        "object": "error",
        "message": {
            "detail": [
                {
                    "type": "extra_forbidden",
                    "loc": ["body", "seed"],
                    "msg": "Extra inputs are not permitted",
                    "input": "The temperature dropped",
                }
            ]
        },
        "type": "invalid_request_error",
        "param": None,
        "code": None,
        "raw_status_code": 422,
    },
    "pydantic-input-echo": {
        "object": "error",
        "message": {
            "detail": [
                {
                    "type": "string_too_long",
                    "loc": ["body", "messages", 0, "content"],
                    "msg": "String should have at most 40 characters",
                    "input": "The temperature dropped; something unknown stood behind me",
                }
            ]
        },
        "type": "invalid_request_error",
        "param": None,
        "code": None,
        "raw_status_code": 422,
    },
    "context-length": {
        "error": {
            "message": "This model's maximum context length is 8192 tokens. However, you "
            "requested 9000 tokens (8936 in the messages, 64 in the completion). Please reduce "
            "the length of the messages or completion.",
            "type": "invalid_request_error",
            "param": "messages",
            "code": "context_length_exceeded",
        }
    },
    # Names max_tokens, but as too large, not as unsupported.
    "tokens-too-large": {
        "object": "error",
        "message": "'max_tokens' or 'max_completion_tokens' is too large: 64. This model's "
        "maximum context length is 4096 tokens and your request has 4090 input tokens.",
        "type": "BadRequestError",
        "param": None,
        "code": 400,
    },
    "value-out-of-range": {
        "error": {
            "message": "Invalid 'temperature': decimal above maximum value. Expected a value "
            "<= 2, but got 3 instead.",
            "type": "invalid_request_error",
            "param": "temperature",
            "code": "decimal_above_max_value",
        }
    },
    "unknown-model": {"error": {"message": "Unknown model: m", "code": "model_not_found"}},
    # Names max_completion_tokens, which the request does not carry;
    # "max_tokens" must not match inside it.
    "max-completion-tokens": _unsupported("max_completion_tokens"),
    # A parameter that is never adapted.
    "other-parameter": _unsupported("seed"),
    # Longer names that contain one of the parameters, after or before it.
    "longer-max-tokens": _unsupported("max_tokens_to_sample"),
    "longer-top-p": _unsupported("top_p_min"),
    "longer-temperature": {"error": {"message": "Unknown parameter: 'temperature_scaling'."}},
    "prefixed-temperature": _unsupported("default_temperature"),
    # The parameter's name and a rejection's words only in a field that
    # repeats the model's output (Groq) or the request (a validator's input).
    "failed-generation": {
        "error": {
            "message": "Failed to generate JSON. Please adjust your prompt. See "
            "'failed_generation' for more details.",
            "type": "invalid_request_error",
            "code": "json_validate_failed",
            "failed_generation": "The temperature dropped; something unknown stood behind me",
        }
    },
    "validator-input": {
        "detail": [
            {
                "type": "string_too_long",
                "loc": ["body", "messages", 0, "content"],
                "msg": "String should have at most 40 characters",
                "input": "The temperature dropped; something unknown stood behind me",
            }
        ]
    },
}


@pytest.mark.parametrize("status", [400, 422])
@pytest.mark.parametrize("error", _OTHER_ERRORS.values(), ids=_OTHER_ERRORS.keys())
async def test_any_other_400_or_422_is_raised_as_it_is(error, status, caplog):
    provider = _Provider("messages", error=lambda _name: error, status=status)

    with caplog.at_level(logging.WARNING, logger=chat_request.__name__):
        for _ in range(2):
            with pytest.raises(_SDK_ERRORS[status]):
                await create_chat_completion(_sdk(provider), _FULL)

    # One attempt per call, and nothing remembered.
    assert provider.bodies == [_FULL, _FULL]
    assert _warnings(caplog) == []


@pytest.mark.parametrize("status", [400, 422])
async def test_an_adjustment_that_did_not_help_is_not_remembered(status, caplog):
    # vLLM puts the validator's errors, input included, in its message: text
    # from the user can read like a rejected parameter.
    echo = "The temperature dropped; something unknown stood behind me"
    error = {
        "object": "error",
        "message": f"[{{'type': 'string_too_long', 'loc': ('body', 'messages', 0, 'content'), "
        f"'msg': 'String should have at most 40 characters', 'input': {echo!r}}}]",
        "type": "BadRequestError",
        "param": None,
        "code": 400,
    }
    provider = _Provider("messages", error=lambda _name: error, status=status)
    client = _sdk(provider)

    with caplog.at_level(logging.WARNING, logger=chat_request.__name__):
        for _ in range(2):
            with pytest.raises(_SDK_ERRORS[status]):
                await create_chat_completion(client, _FULL)

    # Sent again without temperature and rejected the same way: one extra
    # request per call, and nothing remembered.
    assert provider.bodies == [_FULL, _adjusted(_FULL, "temperature")] * 2
    assert chat_request._adjusted == {}
    assert _warnings(caplog) == []


def _vllm_error(message: str) -> dict[str, Any]:
    """vLLM's 400 (or a 422 like it): the validator's errors, input included,
    as one string."""
    return {"object": "error", "message": message, "type": "BadRequestError", "param": None}


@pytest.mark.parametrize("status", [400, 422])
async def test_the_same_error_again_after_an_adjustment_is_raised(status, caplog):
    # The echoed text names two parameters the request carries and reads
    # like a rejection: still there once the first is left out, it is not a
    # rejection of the second.
    echo = "my temperature rose and the response_format of my dream was unknown"
    error = _vllm_error(
        f"[{{'type': 'string_too_long', 'loc': ('body', 'messages', 0, 'content'), "
        f"'msg': 'String should have at most 40 characters', 'input': {echo!r}}}]"
    )
    provider = _Provider("messages", error=lambda _name: error, status=status)

    with caplog.at_level(logging.WARNING, logger=chat_request.__name__):
        for _ in range(2):
            with pytest.raises(_SDK_ERRORS[status], match="string_too_long"):
                await create_chat_completion(_sdk(provider), _FULL)

    assert provider.bodies == [_FULL, _adjusted(_FULL, "temperature")] * 2
    assert chat_request._adjusted == {}
    assert _warnings(caplog) == []


@pytest.mark.parametrize(
    "request_", [_FULL, {**_FULL, "max_tokens": 1, "temperature": 0.0}], ids=["full", "values"]
)
async def test_the_same_error_echoing_the_whole_request_is_raised(request_, caplog):
    # A validator of the whole body echoes it, every parameter's name and
    # value in it, and the user's text reads like a rejection. Leaving a
    # parameter out (or renaming the limit) only changes the echo.
    request = {**request_, "messages": [{"role": "user", "content": "Something unknown came"}]}
    bodies: list[dict[str, Any]] = []

    def endpoint(http_request: httpx.Request) -> httpx.Response:
        body = json.loads(http_request.content)
        bodies.append(body)
        return httpx.Response(
            400,
            json=_vllm_error(
                f"[{{'type': 'value_error', 'loc': ('body',), 'msg': 'Value error, too many "
                f"messages', 'input': {body!r}, 'ctx': {{'error': ValueError('too many')}}}}]"
            ),
        )

    with caplog.at_level(logging.WARNING, logger=chat_request.__name__):
        with pytest.raises(openai.BadRequestError, match="too many messages"):
            await create_chat_completion(_sdk(endpoint), request)

    assert len(bodies) == 2
    assert bodies[0] == request
    assert chat_request._adjusted == {}
    assert _warnings(caplog) == []


def _validator(
    fields: dict[str, tuple[str, str]], *, mistral: bool
) -> tuple[Callable[[httpx.Request], httpx.Response], list[dict[str, Any]]]:
    """A pydantic endpoint: a 422 listing, one by one, each of `fields`
    ({name: (type, msg)}) the body carries, as api.mistral.ai wraps it or as
    FastAPI sends it; anything else is answered. Also the bodies it got."""
    bodies: list[dict[str, Any]] = []

    def endpoint(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        detail = [
            {"type": kind, "loc": ["body", name], "msg": msg, "input": body[name]}
            for name, (kind, msg) in fields.items()
            if name in body
        ]
        if not detail:
            return httpx.Response(200, json=_completion(_VERDICT))
        if mistral:
            return httpx.Response(422, json={"object": "error", "message": {"detail": detail}})
        return httpx.Response(422, json={"detail": detail})

    return endpoint, bodies


_OUT_OF_RANGE = ("greater_than", "Input should be greater than 0")
_EXTRA_FIELD = ("extra_forbidden", "Extra inputs are not permitted")
_SHAPES = pytest.mark.parametrize("mistral", [True, False], ids=["mistral", "fastapi"])


@_SHAPES
@pytest.mark.parametrize("first", ["temperature", "response_format"])
async def test_a_validators_errors_are_read_one_by_one(mistral, first, caplog):
    # temperature out of range and response_format not taken, in one 422: the
    # rejection's words are response_format's alone.
    fields = {"temperature": _OUT_OF_RANGE, "response_format": _EXTRA_FIELD}
    if first == "response_format":
        fields = dict(reversed(fields.items()))
    endpoint, bodies = _validator(fields, mistral=mistral)
    request = {**_FULL, "temperature": 0.0}

    with caplog.at_level(logging.WARNING, logger=chat_request.__name__):
        with pytest.raises(openai.UnprocessableEntityError, match="greater than 0"):
            await create_chat_completion(_sdk(endpoint), request)

    # Only response_format is adjusted; the value out of range is raised as
    # it is, and temperature never left out.
    assert bodies == [request, _adjusted(request, "response_format")]
    assert chat_request._adjusted == {}
    assert _warnings(caplog) == []


@_SHAPES
async def test_a_value_out_of_range_beside_another_field_is_raised_at_once(mistral):
    endpoint, bodies = _validator(
        {"temperature": _OUT_OF_RANGE, "seed": _EXTRA_FIELD}, mistral=mistral
    )
    request = {**_FULL, "temperature": 0.0, "seed": 7}

    with pytest.raises(openai.UnprocessableEntityError):
        await create_chat_completion(_sdk(endpoint), request)

    assert bodies == [request]


@_SHAPES
async def test_every_field_a_validator_rejects_at_once_is_adjusted(mistral, caplog):
    endpoint, bodies = _validator(
        {"reasoning_effort": _EXTRA_FIELD, "response_format": _EXTRA_FIELD}, mistral=mistral
    )
    client = _sdk(endpoint)

    with caplog.at_level(logging.WARNING, logger=chat_request.__name__):
        await create_chat_completion(client, _FULL)
        await create_chat_completion(client, _FULL)

    both = _adjusted(_FULL, "reasoning_effort", "response_format")
    assert bodies == [_FULL, _adjusted(_FULL, "reasoning_effort"), both, both]
    assert len(_warnings(caplog)) == 2


async def test_parameters_listed_in_one_message_are_each_adjusted(caplog):
    # Some proxies name every parameter they do not take in one message; the
    # message changes as each is left out.
    def error(_name: str) -> dict[str, Any]:
        names = [n for n in ("temperature", "top_p") if n in provider.bodies[-1]]
        return {"error": {"message": f"m does not support parameters: {names}, for model=m"}}

    provider = _Provider("temperature", "top_p", error=error)

    with caplog.at_level(logging.WARNING, logger=chat_request.__name__):
        await create_chat_completion(_sdk(provider), _FULL)

    assert provider.bodies == [
        _FULL,
        _adjusted(_FULL, "temperature"),
        _adjusted(_FULL, "temperature", "top_p"),
    ]
    assert len(_warnings(caplog)) == 2


async def test_an_adjustment_is_not_remembered_when_its_request_fails(caplog):
    answers = iter(
        [
            httpx.Response(400, json=_unsupported("temperature")),
            httpx.Response(500, json={"error": {"message": "The server is overloaded."}}),
        ]
    )
    bodies: list[dict[str, Any]] = []

    def endpoint(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return next(answers)

    with caplog.at_level(logging.WARNING, logger=chat_request.__name__):
        with pytest.raises(openai.InternalServerError):
            await create_chat_completion(_sdk(endpoint), _FULL)

    assert bodies == [_FULL, _adjusted(_FULL, "temperature")]
    assert chat_request._adjusted == {}
    assert _warnings(caplog) == []


async def test_a_remembered_adjustment_applies_to_requests_that_carry_the_parameter():
    provider = _Provider("response_format")
    client = _sdk(provider)
    await create_chat_completion(client, _FULL)
    provider.bodies.clear()

    plain = {"model": "m", "messages": _MESSAGES, "temperature": 0.2}
    await create_chat_completion(client, plain)

    assert provider.bodies == [plain]


async def test_a_parameter_the_request_does_not_carry_is_not_retried():
    provider = _Provider("messages", error=lambda _name: _unsupported("top_p"))
    request = {k: v for k, v in _FULL.items() if k != "top_p"}

    with pytest.raises(openai.BadRequestError):
        await create_chat_completion(_sdk(provider), request)

    assert provider.bodies == [request]


@pytest.mark.parametrize(
    ("status", "sdk_error"),
    [
        (401, openai.AuthenticationError),
        (403, openai.PermissionDeniedError),
        (404, openai.NotFoundError),
        (409, openai.ConflictError),
        (429, openai.RateLimitError),
    ],
)
@pytest.mark.parametrize("error", [_unsupported, _extra_forbidden])
async def test_only_a_400_or_422_is_adapted(status, sdk_error, error):
    provider = _Provider("temperature", status=status, error=error)

    with pytest.raises(sdk_error):
        await create_chat_completion(_sdk(provider), _FULL)

    assert provider.bodies == [_FULL]


@pytest.mark.parametrize(("status", "error"), _REJECTIONS.values(), ids=_REJECTIONS.keys())
async def test_at_most_three_adaptations_per_call(status, error, caplog):
    # Rejects every parameter it is sent, one at a time.
    names = ("temperature", "top_p", "max_tokens", "response_format", "reasoning_effort")
    provider = _Provider(*names, status=status, error=error)
    client = _sdk(provider)

    with caplog.at_level(logging.WARNING, logger=chat_request.__name__):
        with pytest.raises(_SDK_ERRORS[status], match="response_format"):
            await create_chat_completion(client, _FULL)

        assert provider.bodies == [
            _FULL,
            _adjusted(_FULL, "temperature"),
            _adjusted(_FULL, "temperature", "top_p"),
            _adjusted(_FULL, "temperature", "top_p", "max_tokens"),
        ]

        # The three adjustments made are kept (the endpoint got past each of
        # them): the next call starts from them and has three of its own.
        provider.bodies.clear()
        await create_chat_completion(client, _FULL)

    assert provider.bodies == [
        _adjusted(_FULL, "temperature", "top_p", "max_tokens"),
        _adjusted(_FULL, "temperature", "top_p", "max_tokens", "response_format"),
        _adjusted(_FULL, *names),
    ]
    assert len(_warnings(caplog)) == 5  # one per parameter


async def test_adjustments_are_kept_per_endpoint_and_model(caplog):
    provider = _Provider("temperature")
    request = {"model": "m", "messages": _MESSAGES, "temperature": 0.2}

    with caplog.at_level(logging.WARNING, logger=chat_request.__name__):
        await create_chat_completion(_sdk(provider), request)
        provider.rejects = ()  # every request is answered from here on
        provider.bodies.clear()
        await create_chat_completion(_sdk(provider), request)
        await create_chat_completion(_sdk(provider), {**request, "model": "other"})
        await create_chat_completion(_sdk(provider, "https://other.example.test/v1"), request)

    assert provider.bodies == [
        {"model": "m", "messages": _MESSAGES},
        {"model": "other", "messages": _MESSAGES, "temperature": 0.2},
        {"model": "m", "messages": _MESSAGES, "temperature": 0.2},
    ]
    assert len(_warnings(caplog)) == 1


async def test_one_warning_per_model_and_parameter(caplog):
    provider = _Provider("temperature")
    request = {"model": "m", "messages": _MESSAGES, "temperature": 0.2}

    with caplog.at_level(logging.WARNING, logger=chat_request.__name__):
        await create_chat_completion(_sdk(provider), request)
        await create_chat_completion(_sdk(provider, "https://other.example.test/v1"), request)

    # Each endpoint finds out for itself; the warning is given once.
    assert provider.bodies == [request, _adjusted(request, "temperature")] * 2
    assert len(_warnings(caplog)) == 1


async def test_a_422_and_a_400_count_against_the_same_three(caplog):
    answers = iter(
        [
            httpx.Response(422, json=_extra_forbidden("reasoning_effort")),
            httpx.Response(400, json=_unsupported("temperature")),
            httpx.Response(422, json=_pydantic_extra_forbidden("top_p")),
            httpx.Response(400, json=_unsupported("response_format")),
        ]
    )
    bodies: list[dict[str, Any]] = []

    def endpoint(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return next(answers)

    with pytest.raises(openai.BadRequestError, match="response_format"):
        await create_chat_completion(_sdk(endpoint), _FULL)

    assert bodies == [
        _FULL,
        _adjusted(_FULL, "reasoning_effort"),
        _adjusted(_FULL, "reasoning_effort", "temperature"),
        _adjusted(_FULL, "reasoning_effort", "temperature", "top_p"),
    ]


# --- max_tokens and max_completion_tokens --------------------------------------------

# _FULL with its limit as max_completion_tokens (a reasoning model's request).
_FULL_MCT = _adjusted(_FULL, "max_tokens")


@pytest.mark.parametrize(("status", "error"), _REJECTIONS.values(), ids=_REJECTIONS.keys())
async def test_a_rejected_max_completion_tokens_is_sent_as_max_tokens(status, error, caplog):
    provider = _Provider("max_completion_tokens", status=status, error=error)
    client = _sdk(provider)

    with caplog.at_level(logging.WARNING, logger=chat_request.__name__):
        await create_chat_completion(client, _FULL_MCT)
        await create_chat_completion(client, _FULL_MCT)

    # The same value under the other name; the next call goes out renamed at once.
    assert provider.bodies == [_FULL_MCT, _FULL, _FULL]
    (warning,) = _warnings(caplog)
    assert "parameter=max_completion_tokens — sending max_tokens instead" in warning


async def test_a_reasoning_model_on_mistral(monkeypatch, caplog):
    # A Magistral model listed as a reasoning model: Mistral's chat completions
    # take max_tokens, not max_completion_tokens, and answer 422.
    monkeypatch.setattr(settings, "llm_reasoning_models", "magistral-medium-2509")
    provider = _Provider("max_completion_tokens", status=422, error=_extra_forbidden)
    llm = _agent("magistral-medium-2509", provider)

    with caplog.at_level(logging.WARNING, logger=chat_request.__name__):
        await llm.generate(_USER_TEXT)
        await llm.generate(_USER_TEXT)

    limits = [(b.get("max_completion_tokens"), b.get("max_tokens")) for b in provider.bodies]
    # The headroom stays: only the name changes.
    assert limits == [(1024 + _EXTRA, None), (None, 1024 + _EXTRA), (None, 1024 + _EXTRA)]
    assert all("temperature" not in b and b["reasoning_effort"] == "low" for b in provider.bodies)
    (warning,) = _warnings(caplog)
    assert "model=magistral-medium-2509 parameter=max_completion_tokens" in warning
    assert _USER_TEXT not in caplog.text


@pytest.mark.parametrize(
    ("request_", "first"),
    [(_FULL, "max_tokens"), (_FULL_MCT, "max_completion_tokens")],
    ids=["max_tokens-first", "max_completion_tokens-first"],
)
async def test_the_token_limit_names_never_ping_pong(request_, first, caplog):
    # Takes neither name.
    provider = _Provider("max_tokens", "max_completion_tokens")
    client = _sdk(provider)

    with caplog.at_level(logging.WARNING, logger=chat_request.__name__):
        await create_chat_completion(client, request_)
        await create_chat_completion(client, request_)

    other = _RENAMED[first]
    neither = _adjusted(_FULL, "max_tokens", "max_completion_tokens")
    # Renamed once; the other name rejected too, the limit is left out rather
    # than renamed back. Both are remembered: the next call goes out without.
    assert provider.bodies == [request_, _adjusted(request_, first), neither, neither]
    assert chat_request._adjusted == {
        (str(client.base_url), "m"): {"max_tokens", "max_completion_tokens"}
    }
    assert _warnings(caplog) == [
        f"llm.parameter_rejected model=m parameter={first} — sending {other} instead from now on",
        f"llm.parameter_rejected model=m parameter={other} — leaving it out from now on",
    ]


async def test_a_remembered_rename_is_never_undone():
    provider = _Provider("max_tokens")
    client = _sdk(provider)
    await create_chat_completion(client, _FULL)
    # The endpoint now rejects the new name too.
    provider.rejects = ("max_tokens", "max_completion_tokens")
    provider.bodies.clear()

    await create_chat_completion(client, _FULL)
    await create_chat_completion(client, _FULL)

    neither = _adjusted(_FULL, "max_tokens", "max_completion_tokens")
    assert provider.bodies == [_FULL_MCT, neither, neither]


@pytest.mark.parametrize("status", [400, 422])
async def test_a_contradicting_endpoint_costs_at_most_three_retries(status):
    # Rejects whichever name it gets, and every other parameter.
    names = ("max_tokens", "max_completion_tokens", "temperature", "top_p", "response_format")
    provider = _Provider(*names, status=status)

    with pytest.raises(_SDK_ERRORS[status]):
        await create_chat_completion(_sdk(provider), _FULL)

    assert provider.bodies == [
        _FULL,
        _FULL_MCT,
        _adjusted(_FULL, "max_tokens", "max_completion_tokens"),
        _adjusted(_FULL, "max_tokens", "max_completion_tokens", "temperature"),
    ]


# --- every way a request is sent -----------------------------------------------------


async def test_agent_generate_adapts_and_remembers(monkeypatch, caplog):
    monkeypatch.setattr(settings, "llm_reasoning_models", "thinker")
    provider = _Provider("reasoning_effort")
    llm = _agent("thinker", provider)

    with caplog.at_level(logging.WARNING, logger=chat_request.__name__):
        assert (await llm.generate(_USER_TEXT))[0] == _VERDICT
        await llm.generate(_USER_TEXT)

    assert ["reasoning_effort" in body for body in provider.bodies] == [True, False, False]
    assert all("temperature" not in body for body in provider.bodies)
    (warning,) = _warnings(caplog)
    assert "model=thinker parameter=reasoning_effort" in warning
    assert _USER_TEXT not in caplog.text


async def test_agent_stream_adapts_at_stream_creation():
    provider = _Provider("temperature")
    llm = _agent("m-large", provider)

    items = [item async for item in llm.generate_stream("hi")]

    assert "".join(text for text, _, _ in items) == "Hello there."
    assert [("temperature" in body, body["stream"]) for body in provider.bodies] == [
        (True, True),
        (False, True),
    ]


async def test_crisis_client_stream_adapts_at_stream_creation():
    provider = _Provider("max_tokens")
    llm = _crisis_client(provider)

    chunks = [c async for c in llm.chat_stream(model="m", messages=_MESSAGES, max_tokens=64)]

    assert "".join(c["text"] for c in chunks) == "Hello there."
    assert [(b.get("max_tokens"), b.get("max_completion_tokens")) for b in provider.bodies] == [
        (64, None),
        (None, 64),
    ]
    assert all(b["stream_options"] == {"include_usage": True} for b in provider.bodies)


async def test_summary_extraction_without_json_mode_still_extracts(monkeypatch):
    fields = {"title": "T", "rewritten_summary": "R", "original_summary": "O"}
    provider = _Provider(
        "response_format", answer=f"Here it is:\n```json\n{json.dumps(fields)}\n```"
    )
    monkeypatch.setattr(core_agent, "response_agent", _agent("m-large", provider))

    out = await summary_structured.extract_summary_structured("Title: T", Language.EN)

    assert out is not None
    assert (out.title, out.rewritten_summary, out.original_summary) == ("T", "R", "O")
    assert ["response_format" in body for body in provider.bodies] == [True, False]


async def test_crisis_check_without_json_mode_keeps_its_strict_parser():
    provider = _Provider("response_format", answer=f"Verdict: {_VERDICT}")
    llm = _crisis_client(provider)

    result = await classify(llm=llm, model="m", text=_USER_TEXT, language=Language.EN)

    assert result.risk is SafetyRisk.NONE
    assert ["response_format" in body for body in provider.bodies] == [True, False]
    assert provider.bodies[1]["temperature"] == 0.0

    # Still fail-closed: an example object before the verdict is ambiguous.
    provider.answer = f'Example: {{"risk": "high"}} Verdict: {_VERDICT}'
    with pytest.raises(ValueError, match="2 JSON objects"):
        await classify(llm=llm, model="m", text=_USER_TEXT, language=Language.EN)
    # The adjustment was remembered: one request, already without JSON mode.
    assert "response_format" not in provider.bodies[-1]
    assert len(provider.bodies) == 3
