"""LLM_* settings: one OpenAI-compatible endpoint for every text role.

Pins the defaults, `llm_configured` / `safety_model`, SAFETY_CRITIC_ENABLED,
LLM_JSON_MODE and LLM_REASONING_* at the request level, which model each role
runs on, and that no request the AI side sends ever asks for logprobs. Offline: a fake endpoint
stands in behind every Agent, an httpx.MockTransport behind the crisis client.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from redream_ai import summary_structured
from redream_ai.api.safety import safety_classify
from redream_ai.clinical_safety import critic as critic_mod
from redream_ai.clinical_safety.classifier import CLINICAL_CODER_PROMPT
from redream_ai.clinical_safety.critic import SEGMENTER_PROMPT, SafetyCritic
from redream_ai.config import Settings, settings
from redream_ai.core import agent as core_agent
from redream_ai.core import irt_app
from redream_ai.core.models import ChatInput, Conversation
from redream_ai.core.prompts import REPLAY_CHECK_PROMPT, ROUTING_PROMPT, TALK_GATE_PROMPT
from redream_ai.llm.openai_compatible import OpenAICompatibleLlmClient
from redream_ai.models import Language, SafetyRequest

_ENDPOINT = {
    "llm_base_url": "http://llm.invalid/v1",
    "llm_api_key": "dummy",
    "llm_model": "big-model",
    "llm_small_model": "small-model",
}

# --- defaults and derived values -------------------------------------------------


def test_defaults():
    fields = Settings.model_fields
    for name in ("llm_base_url", "llm_api_key", "llm_model", "llm_small_model"):
        assert fields[name].default == ""
    assert fields["llm_safety_model"].default == ""
    assert fields["llm_timeout_seconds"].default == 60
    assert fields["llm_safety_timeout_seconds"].default == 20
    assert fields["llm_json_mode"].default is True
    assert fields["llm_reasoning_models"].default == ""
    assert fields["llm_reasoning_effort"].default == "low"
    assert fields["llm_reasoning_extra_tokens"].default == 2048
    assert fields["safety_critic_enabled"].default is True
    # The per-provider chat settings are gone; STT/TTS keep theirs (S09).
    assert "scaleway_safety_model" not in fields
    assert "mistral_safety_model" not in fields
    assert {"mistral_api_key", "scaleway_api_key", "scaleway_project_id"} <= set(fields)


def test_llm_configured_needs_the_endpoint_the_key_and_both_models():
    assert Settings(_env_file=None, **_ENDPOINT).llm_configured
    for name in _ENDPOINT:
        incomplete = Settings(_env_file=None, **{**_ENDPOINT, name: ""})
        assert not incomplete.llm_configured, name


def test_safety_model_falls_back_to_the_small_model():
    small_only = Settings(_env_file=None, llm_small_model="small", llm_safety_model="")
    assert small_only.safety_model == "small"
    own = Settings(_env_file=None, llm_small_model="small", llm_safety_model="guard")
    assert own.safety_model == "guard"


def test_values_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("LLM_JSON_MODE", "false")
    monkeypatch.setenv("SAFETY_CRITIC_ENABLED", "false")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("LLM_SAFETY_TIMEOUT_SECONDS", "5.5")
    monkeypatch.setenv("LLM_SAFETY_MODEL", "guard")

    s = Settings(_env_file=None)

    assert s.llm_json_mode is False
    assert s.safety_critic_enabled is False
    assert s.llm_timeout_seconds == 30
    assert s.llm_safety_timeout_seconds == 5.5
    assert s.safety_model == "guard"


def test_reasoning_settings_from_the_environment(monkeypatch):
    monkeypatch.setenv("LLM_REASONING_MODELS", " thinker , other-thinker,, ")
    monkeypatch.setenv("LLM_REASONING_EFFORT", " Medium ")
    monkeypatch.setenv("LLM_REASONING_EXTRA_TOKENS", "4096")

    s = Settings(_env_file=None)

    assert s.reasoning_models == {"thinker", "other-thinker"}
    assert s.llm_reasoning_effort == "medium"
    assert s.llm_reasoning_extra_tokens == 4096
    assert Settings(_env_file=None, llm_reasoning_models="").reasoning_models == frozenset()


@pytest.mark.parametrize("effort", ["", "minimal", "low", "medium", "high"])
def test_reasoning_effort_values(effort):
    assert Settings(_env_file=None, llm_reasoning_effort=effort).llm_reasoning_effort == effort


@pytest.mark.parametrize("effort", ["max", "none", "xhigh", "1"])
def test_unknown_reasoning_effort_is_refused(effort):
    with pytest.raises(ValidationError, match="llm_reasoning_effort"):
        Settings(_env_file=None, llm_reasoning_effort=effort)


def test_reasoning_extra_tokens_values(monkeypatch):
    assert Settings(_env_file=None, llm_reasoning_extra_tokens=0).llm_reasoning_extra_tokens == 0
    monkeypatch.setenv("LLM_REASONING_EXTRA_TOKENS", "-1")
    with pytest.raises(ValidationError, match="llm_reasoning_extra_tokens"):
        Settings(_env_file=None)


# --- SAFETY_CRITIC_ENABLED ---------------------------------------------------------


def test_disabled_critic_is_not_built_and_warns(monkeypatch, caplog):
    built = []
    monkeypatch.setattr(critic_mod, "SafetyCritic", lambda *a, **kw: built.append(1))
    monkeypatch.setattr(settings, "safety_critic_enabled", False)

    with caplog.at_level(logging.WARNING, logger=irt_app.__name__):
        assert irt_app._build_safety_critic() is None

    assert built == []
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("SAFETY_CRITIC_ENABLED" in w for w in warnings), warnings


def test_enabled_critic_is_built():
    assert isinstance(irt_app._build_safety_critic(), SafetyCritic)
    # conftest pins the production default, so the module-level one exists too.
    assert isinstance(irt_app.safety_critic, SafetyCritic)


def test_critic_without_an_endpoint_fails_open(monkeypatch, caplog):
    monkeypatch.setattr(settings, "llm_base_url", "")
    with caplog.at_level(logging.WARNING, logger=irt_app.__name__):
        assert irt_app._build_safety_critic() is None
    assert "LLM_BASE_URL" in caplog.text


# --- a fake endpoint behind every Agent ------------------------------------------

_SUMMARY = {"title": "T", "rewritten_summary": "R", "original_summary": "O"}


class _Stream:
    def __init__(self, texts: list[str]) -> None:
        self._chunks = [
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=t))], usage=None)
            for t in texts
        ]

    async def close(self) -> None:
        return None

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for chunk in self._chunks:
            yield chunk


class _Endpoint:
    """Every Agent's client: records each request and answers by role."""

    def __init__(self, classifier_code: str = "REPLAY") -> None:
        self.requests: list[dict[str, Any]] = []
        self.classifier_code = classifier_code
        self.chat = SimpleNamespace(completions=self)

    def role(self, request: dict[str, Any]) -> str:
        system = request["messages"][0]["content"]
        if system == ROUTING_PROMPT:
            return "routing"
        if system == TALK_GATE_PROMPT:
            return "talk_gate"
        if system == REPLAY_CHECK_PROMPT:
            return "replay_check"
        if system == SEGMENTER_PROMPT:
            return "segmenter"
        if system.startswith(CLINICAL_CODER_PROMPT):
            return "classifier"
        if system == summary_structured._EXTRACT_SYSTEM_PROMPT:
            return "extraction"
        if request["messages"][1]["content"].startswith("Patient's nightmare description:"):
            return "nightmare_summary"
        return "reply"

    async def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        if kwargs.get("stream"):
            return _Stream(["You turn ", "and face it."])
        reply = {
            "routing": "rewriting",
            "talk_gate": "DREAM",
            "replay_check": "CHANGED",
            "segmenter": json.dumps(
                [{"text": "I turn and face it", "type": "ACTION", "context": "the monster"}]
            ),
            "classifier": self.classifier_code,
            "extraction": f"```json\n{json.dumps(_SUMMARY)}\n```",
            "nightmare_summary": "A monster chases you.",
            "reply": "You turn and face it.",
        }[self.role(kwargs)]
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=reply))], usage=None
        )


@pytest.fixture
def endpoint(monkeypatch) -> _Endpoint:
    """Real agents and a real SafetyCritic, built from the settings, over a fake endpoint."""
    for name, value in _ENDPOINT.items():
        monkeypatch.setattr(settings, name, value)
    # A local .env or shell may set it; the crisis check then runs on the small model.
    monkeypatch.setattr(settings, "llm_safety_model", "")
    ep = _Endpoint()
    monkeypatch.setattr(core_agent, "build_client", lambda: ep)
    routing, response = core_agent.build_agents()
    monkeypatch.setattr(irt_app, "routing_agent", routing)
    monkeypatch.setattr(irt_app, "response_agent", response)
    monkeypatch.setattr(core_agent, "response_agent", response)
    monkeypatch.setattr(irt_app, "safety_critic", SafetyCritic())
    monkeypatch.setattr(irt_app, "_talk_verdicts", {})
    return ep


def _conversation() -> Conversation:
    conv = Conversation(session_id="s", language="en", stages=["recording"])
    conv.add_message("A monster chased me through the woods.", "user")
    conv.add_message("What would you like to change?", "assistant", "recording")
    return conv


def _turn() -> ChatInput:
    return ChatInput(session_id="s", message="I turn and face it", language_override="en")


async def _crisis_bodies(monkeypatch) -> list[dict[str, Any]]:
    """One crisis check through the production client, as the endpoint sees it."""
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        answer = json.dumps({"risk": "none", "categories": [], "confidence": 0.9})
        return httpx.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "created": 0,
                "model": "m",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": answer},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    llm = OpenAICompatibleLlmClient(
        "http://llm.invalid/v1",
        "dummy",
        timeout=5.0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    await safety_classify(SafetyRequest(text="He hurt me.", language=Language.EN), llm=llm)
    return bodies


async def test_every_role_runs_on_its_model_and_never_asks_for_logprobs(endpoint, monkeypatch):
    # A rewriting turn: routing, nightmare summary, talk gate, segmenter,
    # classifier ("replay"), replay check, reply — then streamed, then extracted.
    await irt_app.process_chat_message(_turn(), _conversation())
    async for _ in irt_app.process_chat_message_stream(_turn(), _conversation()):
        pass
    await summary_structured.extract_summary_structured("Title: T", Language.EN)

    models = {(endpoint.role(r), r["model"]) for r in endpoint.requests}
    assert models == {
        ("routing", "small-model"),
        ("talk_gate", "small-model"),
        ("replay_check", "small-model"),
        ("segmenter", "small-model"),
        ("classifier", "small-model"),
        ("nightmare_summary", "big-model"),
        ("reply", "big-model"),
        ("extraction", "big-model"),
    }
    crisis = await _crisis_bodies(monkeypatch)
    assert crisis[0]["model"] == settings.safety_model == "small-model"
    for request in [*endpoint.requests, *crisis]:
        assert "logprobs" not in request
        assert "top_logprobs" not in request


# --- LLM_JSON_MODE -----------------------------------------------------------------


@pytest.mark.parametrize("json_mode", [True, False])
async def test_json_mode_decides_response_format(endpoint, monkeypatch, json_mode):
    monkeypatch.setattr(settings, "llm_json_mode", json_mode)

    out = await summary_structured.extract_summary_structured("Title: T", Language.EN)
    crisis = await _crisis_bodies(monkeypatch)

    assert out is not None and out.title == "T"  # fenced JSON parses either way
    (extraction,) = [r for r in endpoint.requests if endpoint.role(r) == "extraction"]
    for request in (extraction, crisis[0]):
        if json_mode:
            assert request["response_format"] == {"type": "json_object"}
        else:
            assert "response_format" not in request


# --- reasoning models ------------------------------------------------------------


async def _every_role(endpoint: _Endpoint, monkeypatch) -> list[dict[str, Any]]:
    """Every request a rewriting turn (plain and streamed), the summary
    extraction and a crisis check send; the crisis body comes last."""
    await irt_app.process_chat_message(_turn(), _conversation())
    async for _ in irt_app.process_chat_message_stream(_turn(), _conversation()):
        pass
    await summary_structured.extract_summary_structured("Title: T", Language.EN)
    assert any(r.get("stream") for r in endpoint.requests)
    return [*endpoint.requests, *await _crisis_bodies(monkeypatch)]


@pytest.mark.parametrize("extra", [0, 2048, 4096])
async def test_regular_models_get_the_parameters_they_always_got(endpoint, monkeypatch, extra):
    # LLM_REASONING_EXTRA_TOKENS is for reasoning models only.
    monkeypatch.setattr(settings, "llm_reasoning_extra_tokens", extra)

    *agent_requests, crisis = await _every_role(endpoint, monkeypatch)

    # The rewrite-safety segmenter and classifier decode greedily.
    temperature = {"segmenter": 0, "classifier": 0}
    for request in agent_requests:
        role = endpoint.role(request)
        expected = {
            "model": request["model"],
            "messages": request["messages"],
            "temperature": temperature.get(role, 0.1),
            "max_tokens": 1024,
        }
        if role == "extraction":
            expected["response_format"] = {"type": "json_object"}
        if request.get("stream"):
            expected["stream"] = True
        assert request == expected, role
    assert crisis == {
        "model": "small-model",
        "messages": crisis["messages"],
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }


_ROLES = {
    "routing",
    "talk_gate",
    "replay_check",
    "segmenter",
    "classifier",
    "nightmare_summary",
    "reply",
    "extraction",
}


@pytest.mark.parametrize("effort", ["low", "high", ""])
async def test_reasoning_models_on_every_role(endpoint, monkeypatch, effort):
    monkeypatch.setattr(settings, "llm_reasoning_models", "big-model,small-model")
    monkeypatch.setattr(settings, "llm_reasoning_effort", effort)

    *agent_requests, crisis = await _every_role(endpoint, monkeypatch)

    assert {endpoint.role(r) for r in agent_requests} == _ROLES
    for request in [*agent_requests, crisis]:
        assert "temperature" not in request
        assert "top_p" not in request
        assert "max_tokens" not in request
        if effort:
            assert request["reasoning_effort"] == effort
        else:
            assert "reasoning_effort" not in request
    for request in agent_requests:
        # The core's limit plus LLM_REASONING_EXTRA_TOKENS (2048) for the reasoning.
        assert request["max_completion_tokens"] == 1024 + 2048
    # The crisis check sets no token limit; JSON mode stays on where it was asked.
    assert "max_completion_tokens" not in crisis
    assert crisis["response_format"] == {"type": "json_object"}
    (extraction,) = [r for r in agent_requests if endpoint.role(r) == "extraction"]
    assert extraction["response_format"] == {"type": "json_object"}


async def test_reasoning_is_decided_per_model(endpoint, monkeypatch):
    monkeypatch.setattr(settings, "llm_reasoning_models", "small-model")
    monkeypatch.setattr(settings, "llm_reasoning_extra_tokens", 0)

    *requests, crisis = await _every_role(endpoint, monkeypatch)

    for request in requests:
        if request["model"] == "small-model":
            assert "temperature" not in request
            assert request["reasoning_effort"] == "low"
            # LLM_REASONING_EXTRA_TOKENS=0: the core's limit as it is.
            assert request["max_completion_tokens"] == 1024
        else:
            assert request["temperature"] == 0.1
            assert request["max_tokens"] == 1024
            assert "reasoning_effort" not in request
    assert {r["model"] for r in requests} == {"small-model", "big-model"}
    assert crisis["model"] == "small-model"
    assert "temperature" not in crisis
