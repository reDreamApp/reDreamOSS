"""Agent-layer tests ported from the SU repo (streamlit-app/tests/test_agent_mistral.py).

Pins the §6 model topology of the integration brief, adapted to the ported core
(redream_ai.core.agent): exact model IDs primary AND fallback for every runtime
role (response, routing, safety segmenter, safety classifier), provider base
URLs, and the Agent fallback/retry mechanics. Fully offline — the OpenAI SDK
client objects are replaced with in-process fakes; no case needed a live API,
so nothing from the original file was dropped.
"""

from __future__ import annotations

import types

import httpx
import pytest
from openai import RateLimitError

from redream_ai.clinical_safety.classifier import ClinicalClassifier
from redream_ai.clinical_safety.critic import SafetyCritic
from redream_ai.core import agent
from redream_ai.core.prompts import ROUTING_PROMPT


def _fake_response(content):
    """Minimal stand-in for an OpenAI chat.completions response object."""
    return types.SimpleNamespace(
        choices=[
            types.SimpleNamespace(
                message=types.SimpleNamespace(content=content),
                logprobs=None,
            )
        ],
        usage=types.SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


def _fake_client(on_create):
    """Build an object exposing .chat.completions.create(**kwargs) -> awaitable."""

    class _Completions:
        async def create(self, **kwargs):
            return on_create(kwargs)

    return types.SimpleNamespace(chat=types.SimpleNamespace(completions=_Completions()))


def _rate_limit_error():
    request = httpx.Request("POST", "https://api.scaleway.ai/v1/chat/completions")
    return RateLimitError(
        "rate limited", response=httpx.Response(429, request=request), body=None
    )


# --- §6 pinned model strings and role wiring (a model-id typo must fail CI) -----


def test_medium_is_default_response_model_on_scaleway():
    assert agent.DEFAULT_RESPONSE_MODEL_KEY == "MISTRAL_MEDIUM"
    assert agent.MODELS[agent.DEFAULT_RESPONSE_MODEL_KEY].name == "mistral-medium-3.5-128b"
    assert agent.MODELS[agent.DEFAULT_RESPONSE_MODEL_KEY].provider == "scaleway"


def test_small_is_default_routing_model_on_scaleway():
    assert agent.DEFAULT_ROUTING_MODEL_KEY == "MISTRAL_SMALL"
    assert (
        agent.MODELS[agent.DEFAULT_ROUTING_MODEL_KEY].name
        == "mistral-small-3.2-24b-instruct-2506"
    )
    assert agent.MODELS[agent.DEFAULT_ROUTING_MODEL_KEY].provider == "scaleway"


def test_fallback_models_are_direct_mistral():
    # Response falls back to mistral-medium-latest on the direct Mistral API — a
    # DELIBERATE floating alias (user decision 2026-07-02, recorded ADR 0010
    # exception) so the rarely-hit fallback tracks Mistral's current medium.
    assert agent.RESPONSE_FALLBACK_MODEL_KEY == "MISTRAL_MEDIUM_DIRECT"
    assert agent.MODELS["MISTRAL_MEDIUM_DIRECT"].name == "mistral-medium-latest"
    assert agent.MODELS["MISTRAL_MEDIUM_DIRECT"].provider == "mistral"
    # Routing/segmenter fall back to Ministral 8B; classifier to direct Mistral Small
    assert agent.ROUTING_FALLBACK_MODEL_KEY == "MINISTRAL_8B"
    assert agent.MODELS["MINISTRAL_8B"].name == "ministral-8b-2512"
    assert agent.MODELS["MINISTRAL_8B"].provider == "mistral"
    assert agent.MODELS["MISTRAL_SMALL_DIRECT"].name == "mistral-small-2603"
    assert agent.MODELS["MISTRAL_SMALL_DIRECT"].provider == "mistral"


def test_nemo_is_retired():
    assert "MISTRAL_NEMO" not in agent.MODELS


def test_module_level_agents_use_pinned_wiring():
    # conftest pins dummy keys before import, so the module singletons exist offline.
    assert agent.routing_agent is not None
    assert agent.response_agent is not None
    assert agent.routing_agent.model == "mistral-small-3.2-24b-instruct-2506"
    assert agent.routing_agent.provider == "scaleway"
    assert agent.routing_agent.fallback_model == "ministral-8b-2512"
    assert agent.routing_agent.fallback_provider == "mistral"
    assert agent.routing_agent.system_prompt == ROUTING_PROMPT
    assert agent.response_agent.model == "mistral-medium-3.5-128b"
    assert agent.response_agent.provider == "scaleway"
    assert agent.response_agent.fallback_model == "mistral-medium-latest"
    assert agent.response_agent.fallback_provider == "mistral"


def test_safety_classifier_wiring_pins():
    clf = ClinicalClassifier()
    assert clf.agent.model == "mistral-small-3.2-24b-instruct-2506"
    assert clf.agent.provider == "scaleway"
    # Deliberately MISTRAL_SMALL_DIRECT, NOT MINISTRAL_8B: the classifier needs
    # logprobs and direct Mistral Small returns them (brief §3).
    assert clf.agent.fallback_model == "mistral-small-2603"
    assert clf.agent.fallback_provider == "mistral"


def test_safety_segmenter_wiring_pins():
    critic = SafetyCritic()
    assert critic.segmenter.model == "mistral-small-3.2-24b-instruct-2506"
    assert critic.segmenter.provider == "scaleway"
    assert critic.segmenter.fallback_model == "ministral-8b-2512"
    assert critic.segmenter.fallback_provider == "mistral"


# --- client construction (base URLs are load-bearing, brief §6) ------------------


def test_mistral_provider_uses_direct_api(monkeypatch):
    captured = {}

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("MISTRAL_API_KEY", "test-mistral-key")
    monkeypatch.setattr(agent, "AsyncOpenAI", FakeAsyncOpenAI)

    llm = agent.Agent(agent.MODELS["MISTRAL_LARGE"], "system prompt")

    assert llm.model == "mistral-large-2512"
    assert llm.provider == "mistral"
    assert llm.fallback_client is None
    assert captured == {
        "api_key": "test-mistral-key",
        "base_url": "https://api.mistral.ai/v1",
    }


def test_scaleway_provider_uses_scaleway_base_url(monkeypatch):
    captured = {}

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("SCALEWAY_API_KEY", "test-scaleway-key")
    monkeypatch.delenv("SCALEWAY_PROJECT_ID", raising=False)
    monkeypatch.setattr(agent, "AsyncOpenAI", FakeAsyncOpenAI)

    llm = agent.Agent(agent.MODELS["MISTRAL_SMALL"], "system prompt")

    assert llm.provider == "scaleway"
    assert captured == {
        "api_key": "test-scaleway-key",
        "base_url": "https://api.scaleway.ai/v1",
    }


def test_scaleway_project_id_scopes_the_base_url(monkeypatch):
    """A key restricted to one project gets 403 on the bare URL (seen live
    2026-10-03): the project has to be part of the path."""
    captured = {}

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("SCALEWAY_API_KEY", "test-scaleway-key")
    monkeypatch.setenv("SCALEWAY_PROJECT_ID", "11111111-2222-3333-4444-555555555555")
    monkeypatch.setattr(agent, "AsyncOpenAI", FakeAsyncOpenAI)

    agent.Agent(agent.MODELS["MISTRAL_SMALL"], "system prompt")

    assert captured["base_url"] == "https://api.scaleway.ai/11111111-2222-3333-4444-555555555555/v1"


def test_mistral_provider_requires_mistral_api_key(monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)

    with pytest.raises(ValueError, match="MISTRAL_API_KEY"):
        agent.Agent(agent.MODELS["MISTRAL_LARGE"], "system prompt")


def test_agent_builds_primary_and_fallback_clients(monkeypatch):
    base_urls = []

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            base_urls.append(kwargs.get("base_url"))

    monkeypatch.setenv("SCALEWAY_API_KEY", "sk")
    monkeypatch.setenv("MISTRAL_API_KEY", "mk")
    monkeypatch.delenv("SCALEWAY_PROJECT_ID", raising=False)
    monkeypatch.setattr(agent, "AsyncOpenAI", FakeAsyncOpenAI)

    llm = agent.Agent(
        agent.MODELS["MISTRAL_SMALL"],
        "system prompt",
        temperature=0,
        fallback_model_config=agent.MODELS["MINISTRAL_8B"],
    )

    assert llm.model == "mistral-small-3.2-24b-instruct-2506"
    assert llm.provider == "scaleway"
    assert llm.fallback_model == "ministral-8b-2512"
    assert llm.fallback_provider == "mistral"
    assert llm.fallback_client is not None
    # Two clients constructed: Scaleway primary + direct Mistral fallback
    assert "https://api.scaleway.ai/v1" in base_urls
    assert "https://api.mistral.ai/v1" in base_urls


# --- generate() mechanics: fallback, retries, usage contract ---------------------


async def test_generate_falls_back_on_primary_error():
    llm = agent.Agent(
        agent.MODELS["MISTRAL_SMALL"],
        "system prompt",
        fallback_model_config=agent.MODELS["MINISTRAL_8B"],
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
    assert fallback_calls and fallback_calls[0]["model"] == "ministral-8b-2512"


async def test_generate_raises_when_no_fallback():
    llm = agent.Agent(agent.MODELS["MISTRAL_LARGE"], "system prompt")  # no fallback configured

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
        agent.MODELS["MISTRAL_SMALL"],
        "system prompt",
        fallback_model_config=agent.MODELS["MINISTRAL_8B"],
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
        agent.MODELS["MISTRAL_SMALL"],
        "constructor prompt",
        fallback_model_config=agent.MODELS["MINISTRAL_8B"],
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
    llm = agent.Agent(agent.MODELS["MISTRAL_LARGE"], "system prompt")

    captured = {}

    def primary_create(kwargs):
        captured.update(kwargs)
        return _fake_response('{"ok": true}')

    llm.client = _fake_client(primary_create)

    content, _, _ = await llm.generate("prose", response_format={"type": "json_object"})

    assert content == '{"ok": true}'
    assert captured["response_format"] == {"type": "json_object"}
    assert captured["model"] == "mistral-large-2512"


def _stream_chunk(content=None, usage=None):
    """Minimal stand-in for an OpenAI streaming chunk."""
    delta = types.SimpleNamespace(content=content)
    choices = [] if content is None else [types.SimpleNamespace(delta=delta)]
    return types.SimpleNamespace(choices=choices, usage=usage)


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
    llm = agent.Agent(agent.MODELS["MISTRAL_LARGE"], "system prompt")

    async def stream():
        yield _stream_chunk("Hello ")
        yield _stream_chunk("there.")
        yield _stream_chunk(usage=provider_usage)

    llm.client = _fake_client(lambda kwargs: stream())

    items = [item async for item in llm.generate_stream("hi")]

    assert [content for content, _, _ in items[:-1]] == ["Hello ", "there."]
    assert items[-1] == ("", expected, None)
