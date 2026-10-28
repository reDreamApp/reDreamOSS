"""SAFETY_DEBUG: every text model call on LLM_SMALL_MODEL (config.py), and the
fallback prompt without the revenge rule (prompts/safety.py, safety-v0.5.1)."""

from __future__ import annotations

import json

import pytest

from redream_ai.config import settings
from redream_ai.core import agent as core_agent
from redream_ai.llm.fake_client import FakeLlmClient
from redream_ai.models import Language
from redream_ai.prompts.safety import _REVENGE_EN, SAFETY_PROMPT_EN, SAFETY_PROMPT_FALLBACK_EN
from redream_ai.safety.classifier import classify

_ENDPOINT = {
    "llm_base_url": "http://llm.invalid/v1",
    "llm_api_key": "dummy",
    "llm_model": "big-model",
    "llm_small_model": "small-model",
    "llm_safety_model": "safety-model",
}


@pytest.fixture
def endpoint(monkeypatch):
    for name, value in _ENDPOINT.items():
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(core_agent, "build_client", lambda: object())


def test_debug_mode_uses_the_small_model_everywhere(endpoint, monkeypatch):
    monkeypatch.setattr(settings, "safety_debug", True)
    monkeypatch.setattr(settings, "environment", "development")
    routing, response = core_agent.build_agents()
    assert routing is not None and response is not None
    assert routing.model == response.model == "small-model"
    assert settings.safety_model == "small-model"


def test_production_ignores_debug_mode(endpoint, monkeypatch):
    monkeypatch.setattr(settings, "safety_debug", True)
    monkeypatch.setattr(settings, "environment", "production")
    _, response = core_agent.build_agents()
    assert response is not None and response.model == "big-model"
    assert settings.safety_model == "safety-model"


class _Down(FakeLlmClient):
    async def chat(self, **kwargs):  # type: ignore[override]
        raise RuntimeError("primary down")


async def test_the_fallback_model_gets_the_prompt_without_the_revenge_rule():
    fallback = FakeLlmClient()
    fallback.enqueue(json.dumps({"risk": "none", "categories": [], "confidence": 0.9}))
    await classify(
        llm=_Down(),
        model="primary",
        text="I dreamt of a forest.",
        language=Language.EN,
        fallback_llm=fallback,
        fallback_model="fallback",
    )
    assert fallback.calls[0]["messages"][0]["content"] == SAFETY_PROMPT_FALLBACK_EN
    assert all(passage in SAFETY_PROMPT_EN for passage in _REVENGE_EN)
    assert not any(passage in SAFETY_PROMPT_FALLBACK_EN for passage in _REVENGE_EN)
