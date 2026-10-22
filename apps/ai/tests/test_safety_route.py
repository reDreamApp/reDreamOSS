"""POST /safety/classify — crisis pre-classifier (ADR 0005).

This path is independent of the ported IRT core and must keep working after the
core replacement. Uses FakeLlmClient via the DI override (no network).
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from redream_ai.api.chat import get_llm_client, get_scaleway_llm_client
from redream_ai.config import settings
from redream_ai.llm.base import LlmUnavailableError
from redream_ai.llm.fake_client import FakeLlmClient
from redream_ai.main import app


@pytest.fixture
def fallback_llm() -> FakeLlmClient:
    return FakeLlmClient()


@pytest.fixture
def client(fake_llm, fallback_llm):
    """`fake_llm` stands in for Scaleway, `fallback_llm` for direct Mistral."""
    app.dependency_overrides[get_scaleway_llm_client] = lambda: fake_llm
    app.dependency_overrides[get_llm_client] = lambda: fallback_llm
    with TestClient(app) as c:
        yield c, fake_llm
    app.dependency_overrides.pop(get_scaleway_llm_client, None)
    app.dependency_overrides.pop(get_llm_client, None)


class _DownLlm(FakeLlmClient):
    async def chat(self, **kwargs):
        raise LlmUnavailableError("down")


_NONE = json.dumps({"risk": "none", "categories": [], "confidence": 0.9})


def test_safety_model_pins():
    """ADR 0010 (2026-10-21 amendment): a changed default is a model change
    and needs the ADR and a live eval run."""
    from redream_ai.config import Settings

    fields = Settings.model_fields
    assert fields["scaleway_safety_model"].default == "mistral-small-3.2-24b-instruct-2506"
    assert fields["mistral_safety_model"].default == "ministral-8b-2512"


def test_scaleway_answers_first(client, fallback_llm):
    tc, fake = client
    fake.enqueue(_NONE)
    r = tc.post("/safety/classify", json={"text": "I dreamt of a forest.", "language": "en"})
    assert r.status_code == 200
    assert [c["model"] for c in fake.calls] == [settings.scaleway_safety_model]
    assert fallback_llm.calls == []


@pytest.mark.parametrize("primary_reply", [None, "Sorry, I cannot help with that."])
def test_mistral_answers_when_scaleway_fails(client, fallback_llm, primary_reply):
    """An outage and an answer that is not the JSON both go to the fallback:
    the backend blocks the chat turn when this check fails."""
    tc, fake = client
    if primary_reply is None:
        app.dependency_overrides[get_scaleway_llm_client] = _DownLlm
    else:
        fake.enqueue(primary_reply)
    fallback_llm.enqueue(
        json.dumps({"risk": "high", "categories": ["suicidal_ideation"], "confidence": 0.9})
    )
    r = tc.post(
        "/safety/classify", json={"text": "I don't want to be here anymore.", "language": "en"}
    )
    assert r.status_code == 200
    assert r.json()["risk"] == "high"
    assert [c["model"] for c in fallback_llm.calls] == [settings.mistral_safety_model]


def test_both_providers_down_is_503(client):
    tc, _ = client
    app.dependency_overrides[get_scaleway_llm_client] = _DownLlm
    app.dependency_overrides[get_llm_client] = _DownLlm
    r = tc.post("/safety/classify", json={"text": "I dreamt of a forest.", "language": "en"})
    assert r.status_code == 503


def test_safety_classify_low_risk(client):
    tc, fake = client
    fake.enqueue(json.dumps({"risk": "none", "categories": [], "confidence": 0.95}))
    r = tc.post("/safety/classify", json={"text": "I dreamt of a calm forest.", "language": "en"})
    assert r.status_code == 200
    body = r.json()
    assert body["risk"] == "none"
    assert body["categories"] == []


def test_safety_classify_high_risk(client):
    tc, fake = client
    fake.enqueue(
        json.dumps({"risk": "high", "categories": ["suicidal_ideation"], "confidence": 0.9})
    )
    r = tc.post(
        "/safety/classify",
        json={"text": "I don't want to be here anymore.", "language": "en"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["risk"] == "high"
    assert "suicidal_ideation" in body["categories"]


def test_safety_empty_text_rejected(client):
    tc, _ = client
    r = tc.post("/safety/classify", json={"text": "   ", "language": "en"})
    assert r.status_code == 400


@pytest.mark.parametrize("text", ["ja", "Yes.", "yes, exactly", "Nein!", "ja genau"])
def test_bare_answer_skips_the_model(client, text):
    """A plain yes / no is never a risk signal: "Hat er dich gewürgt?" → "ja"
    came back high and showed the helpline instead of a reply (2026-10-14)."""
    tc, fake = client
    r = tc.post(
        "/safety/classify",
        json={
            "text": text,
            "language": "de",
            "stage": "recording",
            "context": "Hat er dich gewürgt?",
        },
    )
    assert r.status_code == 200
    assert r.json()["risk"] == "none"
    assert fake.calls == []


@pytest.mark.parametrize("text", ["8", "yes he did", "yes yes yes yes", "ja, ich will sterben"])
def test_other_short_replies_reach_the_model(client, text):
    tc, fake = client
    fake.enqueue(json.dumps({"risk": "none", "categories": [], "confidence": 0.9}))
    r = tc.post("/safety/classify", json={"text": text, "language": "en"})
    assert r.status_code == 200
    assert len(fake.calls) == 1


@pytest.mark.parametrize(
    ("stage", "labelled"),
    [("recording", True), ("rewriting", True), ("summary", True), ("final", False)],
)
def test_context_marked_as_about_the_dream(client, stage, labelled):
    tc, fake = client
    fake.enqueue(json.dumps({"risk": "none", "categories": [], "confidence": 0.9}))
    tc.post(
        "/safety/classify",
        json={
            "text": "he did it again",
            "language": "en",
            "stage": stage,
            "context": "What happens next?",
        },
    )
    user_turn = fake.calls[0]["messages"][1]["content"]
    assert ("(about the user's dream)" in user_turn) is labelled
    assert "What happens next?" in user_turn
