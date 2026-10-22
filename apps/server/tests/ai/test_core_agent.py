"""Agent-layer tests ported from the SU repo (streamlit-app/tests/test_agent_mistral.py).

Pins the settings -> agent wiring of the ported core (redream_ai.core.agent): one
OpenAI-compatible endpoint (LLM_BASE_URL / LLM_API_KEY, LLM_TIMEOUT_SECONDS on
every client), LLM_MODEL for responses, LLM_SMALL_MODEL for routing and the
rewrite-safety segmenter + classifier, no fallback configured, no agents until
the endpoint is configured — and the Agent fallback/retry mechanics. Fully
offline — the OpenAI SDK client objects are replaced with in-process fakes.
"""

from __future__ import annotations

import contextlib
import importlib.util
import logging
import types

import httpx
import pytest
from openai import RateLimitError

from redream_ai.clinical_safety.classifier import ClinicalClassifier
from redream_ai.clinical_safety.critic import SafetyCritic
from redream_ai.config import settings
from redream_ai.core import agent
from redream_ai.core.prompts import ROUTING_PROMPT

_USAGE = types.SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)


def _fake_response(content, usage=_USAGE):
    """Minimal stand-in for an OpenAI chat.completions response object."""
    return types.SimpleNamespace(
        choices=[
            types.SimpleNamespace(
                message=types.SimpleNamespace(content=content),
                logprobs=None,
            )
        ],
        usage=usage,
    )


def _fake_client(on_create):
    """Build an object exposing .chat.completions.create(**kwargs) -> awaitable."""

    class _Completions:
        async def create(self, **kwargs):
            return on_create(kwargs)

    return types.SimpleNamespace(chat=types.SimpleNamespace(completions=_Completions()))


def _rate_limit_error():
    request = httpx.Request("POST", "http://llm.invalid/v1/chat/completions")
    return RateLimitError("rate limited", response=httpx.Response(429, request=request), body=None)


_ENDPOINT = {
    "llm_base_url": "https://llm.example.test/v1",
    "llm_api_key": "sk-test",
    "llm_model": "big-model",
    "llm_small_model": "small-model",
    "llm_timeout_seconds": 12.5,
}
_CLIENT_KWARGS = {"base_url": "https://llm.example.test/v1", "api_key": "sk-test", "timeout": 12.5}


@pytest.fixture
def endpoint(monkeypatch):
    """A custom endpoint on the settings object every builder reads."""
    for name, value in _ENDPOINT.items():
        monkeypatch.setattr(settings, name, value)


@pytest.fixture
def built_clients(monkeypatch):
    """Every AsyncOpenAI the core builds, as its constructor kwargs."""
    built = []

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            built.append(kwargs)

    monkeypatch.setattr(agent, "AsyncOpenAI", FakeAsyncOpenAI)
    return built


# --- settings -> agent wiring (one endpoint, no registry, no fallback) -----------


def test_registry_and_provider_wiring_are_gone():
    for name in (
        "MODELS",
        "ModelConfig",
        "DEFAULT_ROUTING_MODEL_KEY",
        "DEFAULT_RESPONSE_MODEL_KEY",
        "ROUTING_FALLBACK_MODEL_KEY",
        "RESPONSE_FALLBACK_MODEL_KEY",
    ):
        assert not hasattr(agent, name), name
    assert importlib.util.find_spec("groq") is None
    assert importlib.util.find_spec("mistralai") is None


def test_build_client_uses_the_llm_settings(endpoint, built_clients):
    agent.build_client()
    # The timeout is load-bearing: the SDK default is 600 s.
    assert built_clients == [_CLIENT_KWARGS]


def test_agents_come_from_the_llm_settings(endpoint, built_clients):
    routing, response = agent.build_agents()

    assert routing.model == "small-model"
    assert routing.system_prompt == ROUTING_PROMPT
    assert routing.temperature == 0.1
    assert response.model == "big-model"
    assert response.temperature == 0.1
    for built in (routing, response):
        assert built.fallback_model is None
        assert built.fallback_client is None
    assert built_clients == [_CLIENT_KWARGS, _CLIENT_KWARGS]


def test_safety_critic_runs_on_the_small_model(endpoint, built_clients):
    critic = SafetyCritic()

    assert critic.segmenter.model == "small-model"
    assert critic.segmenter.temperature == 0
    assert critic.classifier.agent.model == "small-model"
    assert critic.classifier.agent.temperature == 0
    for built in (critic.segmenter, critic.classifier.agent):
        assert built.fallback_model is None
        assert built.fallback_client is None
    assert built_clients == [_CLIENT_KWARGS, _CLIENT_KWARGS]
    # An explicit model still wins (evals, thesis runs).
    assert ClinicalClassifier(model="other-model").agent.model == "other-model"


def test_module_level_agents_use_the_pinned_endpoint():
    # conftest pins a dummy endpoint before import, so the module singletons exist offline.
    assert agent.routing_agent is not None
    assert agent.response_agent is not None
    assert agent.routing_agent.model == settings.llm_small_model
    assert agent.response_agent.model == settings.llm_model
    for built in (agent.routing_agent, agent.response_agent):
        assert str(built.client.base_url).rstrip("/") == settings.llm_base_url.rstrip("/")
        assert built.client.timeout == settings.llm_timeout_seconds
        assert built.fallback_client is None


@pytest.mark.parametrize("missing", ["llm_base_url", "llm_api_key", "llm_model", "llm_small_model"])
def test_no_agents_until_the_endpoint_is_configured(
    endpoint, built_clients, monkeypatch, caplog, missing
):
    monkeypatch.setattr(settings, missing, "")

    with caplog.at_level(logging.WARNING, logger=agent.__name__):
        assert agent.build_agents() == (None, None)

    assert built_clients == []
    assert "LLM_BASE_URL, LLM_API_KEY, LLM_MODEL and LLM_SMALL_MODEL" in caplog.text
    with pytest.raises(ValueError, match="LLM_BASE_URL"):
        agent.Agent("m", "system prompt")


def test_fallback_model_shares_the_endpoint(endpoint, built_clients):
    llm = agent.Agent("small-model", "system prompt", fallback_model="other-model")

    assert llm.fallback_model == "other-model"
    assert llm.fallback_client is llm.client
    assert built_clients == [_CLIENT_KWARGS]


# --- generate() mechanics: fallback, retries, usage contract ---------------------


async def test_generate_falls_back_on_primary_error():
    llm = agent.Agent(
        "m-small",
        "system prompt",
        fallback_model="m-fallback",
    )

    primary_calls = []
    fallback_calls = []

    def primary_create(kwargs):
        primary_calls.append(kwargs)
        raise RuntimeError("primary down")

    def fallback_create(kwargs):
        fallback_calls.append(kwargs)
        return _fake_response("FALLBACK_OK")

    llm.client = _fake_client(primary_create)
    llm.fallback_client = _fake_client(fallback_create)

    content, usage, logprobs = await llm.generate("hello")

    assert content == "FALLBACK_OK"
    assert logprobs is None
    # usage keys are exactly {"input","output","total"} — consumed verbatim (brief §6)
    assert usage == {"input": 1, "output": 1, "total": 2}
    # Non-transient errors are NOT retried: exactly one primary attempt
    assert len(primary_calls) == 1
    # Fallback was called with the fallback model id, not the primary's
    assert fallback_calls and fallback_calls[0]["model"] == "m-fallback"


async def test_generate_raises_when_no_fallback():
    llm = agent.Agent("m-large", "system prompt")  # no fallback configured

    def primary_create(kwargs):
        raise RuntimeError("primary down")

    llm.client = _fake_client(primary_create)

    with pytest.raises(RuntimeError, match="primary down"):
        await llm.generate("hello")


async def test_transient_error_is_retried_on_primary_before_any_fallback(monkeypatch):
    async def _no_sleep(_delay):
        return None

    monkeypatch.setattr(agent.asyncio, "sleep", _no_sleep)

    llm = agent.Agent(
        "m-small",
        "system prompt",
        fallback_model="m-fallback",
    )

    primary_calls = []

    def primary_create(kwargs):
        primary_calls.append(kwargs)
        if len(primary_calls) == 1:
            raise _rate_limit_error()
        return _fake_response("PRIMARY_OK")

    def fallback_create(kwargs):
        raise AssertionError("fallback must not fire when a transient retry succeeds")

    llm.client = _fake_client(primary_create)
    llm.fallback_client = _fake_client(fallback_create)

    content, _, _ = await llm.generate("hello")

    assert content == "PRIMARY_OK"
    assert len(primary_calls) == 2  # one transient failure + one successful retry


async def test_per_call_system_prompt_override_feeds_primary_and_fallback():
    # §7.1 concurrency fix: the composed system prompt is passed per call — and the
    # exact same override must reach the fallback request, not the constructor's.
    llm = agent.Agent(
        "m-small",
        "constructor prompt",
        fallback_model="m-fallback",
    )

    captured = {}

    def primary_create(kwargs):
        captured["primary"] = kwargs
        raise RuntimeError("primary down")

    def fallback_create(kwargs):
        captured["fallback"] = kwargs
        return _fake_response("ok")

    llm.client = _fake_client(primary_create)
    llm.fallback_client = _fake_client(fallback_create)

    await llm.generate("user turn", system_prompt="PER-CALL OVERRIDE")

    for kwargs in (captured["primary"], captured["fallback"]):
        assert kwargs["messages"][0] == {"role": "system", "content": "PER-CALL OVERRIDE"}
        assert kwargs["messages"][1] == {"role": "user", "content": "user turn"}
    assert llm.system_prompt == "constructor prompt"  # never mutated


async def test_generate_passes_response_format_through():
    # Structured-output spec (used by the summary extraction pass, ADR 0003).
    llm = agent.Agent("m-large", "system prompt")

    captured = {}

    def primary_create(kwargs):
        captured.update(kwargs)
        return _fake_response('{"ok": true}')

    llm.client = _fake_client(primary_create)

    content, _, _ = await llm.generate("prose", response_format={"type": "json_object"})

    assert content == '{"ok": true}'
    assert captured["response_format"] == {"type": "json_object"}
    assert captured["model"] == "m-large"


async def test_request_carries_the_parameters_the_core_always_sent():
    llm = agent.Agent("m-large", "system prompt", temperature=0.1)
    captured = {}

    def primary_create(kwargs):
        captured.update(kwargs)
        return _fake_response("ok")

    llm.client = _fake_client(primary_create)

    await llm.generate("hello")

    assert captured == {
        "model": "m-large",
        "messages": [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "hello"},
        ],
        "temperature": 0.1,
        "max_tokens": 1024,
    }


@pytest.mark.parametrize(
    "usage",
    [None, types.SimpleNamespace(prompt_tokens=None, completion_tokens=None, total_tokens=None)],
)
async def test_none_content_and_none_usage_are_tolerated(usage):
    """Some OpenAI-compatible endpoints leave out the usage block (or its
    counts) or answer with content null."""
    llm = agent.Agent("m-large", "system prompt")
    llm.client = _fake_client(lambda kwargs: _fake_response(None, usage=usage))

    assert await llm.generate("hello") == ("", {"input": 0, "output": 0, "total": 0}, None)


def _stream_chunk(content=None, usage=None):
    """Minimal stand-in for an OpenAI streaming chunk."""
    delta = types.SimpleNamespace(content=content)
    choices = [] if content is None else [types.SimpleNamespace(delta=delta)]
    return types.SimpleNamespace(choices=choices, usage=usage)


def _odd_chunk(choices):
    """A chunk some endpoints send between deltas: no choices at all, choices
    of None, a choice without a delta, or a delta with content None."""
    return types.SimpleNamespace(choices=choices, usage=None)


class _FakeStream:
    """Minimal stand-in for the SDK's AsyncStream: async-iterable, with an async
    close() that closes the HTTP response (optionally failing while it does)."""

    def __init__(self, chunks, close_error=None):
        self._chunks = chunks
        self._close_error = close_error
        self.closed = False

    async def close(self):
        self.closed = True
        if self._close_error is not None:
            raise self._close_error

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for chunk in self._chunks:
            yield chunk


@pytest.mark.parametrize(
    ("provider_usage", "expected"),
    [
        (
            types.SimpleNamespace(prompt_tokens=12, completion_tokens=5, total_tokens=17),
            {"input": 12, "output": 5, "total": 17},
        ),
        (None, {"input": 0, "output": 0, "total": 0}),
    ],
)
async def test_stream_usage_is_provider_reported_or_zero(provider_usage, expected):
    llm = agent.Agent("m-large", "system prompt")

    stream = _FakeStream(
        [_stream_chunk("Hello "), _stream_chunk("there."), _stream_chunk(usage=provider_usage)]
    )
    llm.client = _fake_client(lambda kwargs: stream)

    items = [item async for item in llm.generate_stream("hi")]

    assert [content for content, _, _ in items[:-1]] == ["Hello ", "there."]
    assert items[-1] == ("", expected, None)
    assert stream.closed


async def test_stream_chunks_without_choices_or_content_are_skipped():
    llm = agent.Agent("m-large", "system prompt")
    stream = _FakeStream(
        [
            _odd_chunk([]),
            _stream_chunk("Hello "),
            _odd_chunk(None),
            _odd_chunk([types.SimpleNamespace(delta=None)]),
            _odd_chunk([types.SimpleNamespace(delta=types.SimpleNamespace(content=None))]),
            _stream_chunk("there."),
        ]
    )
    llm.client = _fake_client(lambda kwargs: stream)

    items = [item async for item in llm.generate_stream("hi")]

    assert items == [
        ("Hello ", None, None),
        ("there.", None, None),
        ("", {"input": 0, "output": 0, "total": 0}, None),
    ]


async def test_stream_closed_when_consumer_stops_early():
    """A client that goes away mid-answer: closing generate_stream closes the
    provider's response at once, not whenever GC finalizes the generator."""
    llm = agent.Agent("m-large", "system prompt")
    stream = _FakeStream([_stream_chunk("Hello "), _stream_chunk("there.")])
    llm.client = _fake_client(lambda kwargs: stream)

    gen = llm.generate_stream("hi")
    async with contextlib.aclosing(gen):
        assert (await anext(gen))[0] == "Hello "
        assert not stream.closed

    assert stream.closed


async def test_failing_close_neither_breaks_an_early_close_nor_a_full_answer():
    """The provider's close raising (socket already gone) is swallowed: it must
    not replace the GeneratorExit of an early close (the layers above would
    then yield an error frame during aclose -> RuntimeError), nor fail an
    answer that was received in full."""
    llm = agent.Agent("m-large", "system prompt")
    chunks = [_stream_chunk("Hello "), _stream_chunk("there.")]

    early = _FakeStream(chunks, close_error=OSError("close failed"))
    llm.client = _fake_client(lambda kwargs: early)
    gen = llm.generate_stream("hi")
    async with contextlib.aclosing(gen):
        assert (await anext(gen))[0] == "Hello "
    assert early.closed

    full = _FakeStream(chunks, close_error=OSError("close failed"))
    llm.client = _fake_client(lambda kwargs: full)
    items = [item async for item in llm.generate_stream("hi")]
    assert [content for content, _, _ in items] == ["Hello ", "there.", ""]
    assert full.closed
