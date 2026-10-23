"""Health endpoint: liveness, configured or not, plus what is missing.

Answers 200 {"status": "ok"} whatever is configured; llm_ready follows the
chat LLM settings (the same check as /instance's features.chat) and
safety_classifier_enabled the dev flag. No user and no database
(`public_client` fails the test if the route reaches either).
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from redream_api.core.config import settings
from redream_api.main import app
from redream_api.services import ai_client as ai_client_mod
from redream_api.services.ai_client import InProcessAiClient, get_ai_client


async def test_health_returns_ok(public_client) -> None:
    r = await public_client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "llm_ready": True, "safety_classifier_enabled": True}


async def test_llm_not_ready_is_still_ok(public_client, fake_ai) -> None:
    fake_ai.llm_configured = False
    r = await public_client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["llm_ready"] is False


async def test_safety_classifier_flag(public_client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "safety_classifier_enabled", False)
    r = await public_client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["safety_classifier_enabled"] is False


@pytest.mark.parametrize("unset", ["llm_base_url", "llm_api_key", "llm_model", "llm_small_model"])
async def test_nothing_configured_with_the_real_client(public_client, monkeypatch, unset) -> None:
    """The in-process client reads redream_ai's settings.llm_configured: any
    of the four LLM settings missing, and llm_ready / features.chat are false."""
    from redream_ai.config import settings as ai_settings

    monkeypatch.setattr(ai_settings, unset, "")
    app.dependency_overrides.pop(get_ai_client)
    monkeypatch.setattr(ai_client_mod, "_default_client", InProcessAiClient())

    r = await public_client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["llm_ready"] is False
    instance = await public_client.get("/api/v1/instance")
    assert instance.status_code == 200
    assert instance.json()["features"]["chat"] is False


async def test_llm_ready_with_the_real_client(public_client, monkeypatch) -> None:
    app.dependency_overrides.pop(get_ai_client)
    monkeypatch.setattr(ai_client_mod, "_default_client", InProcessAiClient())

    r = await public_client.get("/api/v1/health")
    assert r.json()["llm_ready"] is True
    assert (await public_client.get("/api/v1/instance")).json()["features"]["chat"] is True


async def test_no_dependency_overrides_needed(monkeypatch) -> None:
    """The probe as a container's healthcheck sees it: the bare app, no token."""
    # The route may build the process-wide client; put the old one back after.
    monkeypatch.setattr(ai_client_mod, "_default_client", ai_client_mod._default_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
