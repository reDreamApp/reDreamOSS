"""`safety_classify` — crisis pre-classifier (ADR 0005).

This path is independent of the ported IRT core and must keep working after the
core replacement. A FakeLlmClient is passed in as the endpoint (no network).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import HTTPException

from redream_ai.api.safety import safety_classify
from redream_ai.config import settings
from redream_ai.llm.base import LlmClient, LlmUnavailableError
from redream_ai.llm.fake_client import FakeLlmClient
from redream_ai.models import Language, SafetyRequest, SafetyRisk
from redream_ai.safety import classify as classify_text


@pytest.fixture
def classify(fake_llm):
    """`fake_llm` stands in for the LLM_* endpoint; it can be swapped per call.
    Returns the response as wire JSON."""

    async def run(body: dict[str, Any], *, llm: LlmClient | None = None) -> dict[str, Any]:
        resp = await safety_classify(SafetyRequest.model_validate(body), llm=llm or fake_llm)
        return resp.model_dump(mode="json")

    return run


class _DownLlm(FakeLlmClient):
    async def chat(self, **kwargs):
        raise LlmUnavailableError("down")


_NONE = json.dumps({"risk": "none", "categories": [], "confidence": 0.9})
_HIGH = json.dumps({"risk": "high", "categories": ["suicidal_ideation"], "confidence": 0.9})


async def test_check_runs_on_the_safety_model(classify, fake_llm, monkeypatch):
    monkeypatch.setattr(settings, "llm_safety_model", "guard-model")
    fake_llm.enqueue(_NONE)
    await classify({"text": "I dreamt of a forest.", "language": "en"})
    assert [c["model"] for c in fake_llm.calls] == ["guard-model"]
    assert fake_llm.calls[0]["temperature"] == 0.0


async def test_safety_model_defaults_to_the_small_model(classify, fake_llm, monkeypatch):
    monkeypatch.setattr(settings, "llm_small_model", "small-model")
    monkeypatch.setattr(settings, "llm_safety_model", "")
    fake_llm.enqueue(_NONE)
    await classify({"text": "I dreamt of a forest.", "language": "en"})
    assert [c["model"] for c in fake_llm.calls] == ["small-model"]


@pytest.mark.parametrize(
    ("reply", "expected"),
    [(None, LlmUnavailableError), ("Sorry, I cannot help with that.", ValueError)],
    ids=["outage", "unusable-answer"],
)
async def test_a_failed_check_raises_without_fallback(classify, fake_llm, reply, expected):
    """An outage and an answer that is not the JSON both fail the check: there
    is no fallback, and the backend blocks the chat turn (502). An outage stays
    LlmUnavailableError, which the backend reports as the provider being down."""
    llm = _DownLlm() if reply is None else fake_llm
    if reply is not None:
        fake_llm.enqueue(reply)
    with pytest.raises(expected):
        await classify({"text": "I don't want to be here anymore.", "language": "en"}, llm=llm)


@pytest.mark.parametrize("reply", [None, "Sorry, I cannot help with that."], ids=["down", "prose"])
async def test_classify_keeps_an_optional_fallback(fake_llm, reply):
    """Production passes none; `classify` itself still takes one, for an
    outage and for an unusable answer alike."""
    primary = _DownLlm() if reply is None else fake_llm
    if reply is not None:
        fake_llm.enqueue(reply)
    fallback = FakeLlmClient()
    fallback.enqueue(_HIGH)
    resp = await classify_text(
        llm=primary,
        model="m-small",
        fallback_llm=fallback,
        fallback_model="m-other",
        text="I don't want to be here anymore.",
        language=Language.EN,
    )
    assert resp.risk == SafetyRisk.HIGH
    assert [c["model"] for c in fallback.calls] == ["m-other"]


async def test_safety_classify_low_risk(classify, fake_llm):
    fake_llm.enqueue(json.dumps({"risk": "none", "categories": [], "confidence": 0.95}))
    body = await classify({"text": "I dreamt of a calm forest.", "language": "en"})
    assert body["risk"] == "none"
    assert body["categories"] == []


async def test_safety_classify_high_risk(classify, fake_llm):
    fake_llm.enqueue(
        json.dumps({"risk": "high", "categories": ["suicidal_ideation"], "confidence": 0.9})
    )
    body = await classify({"text": "I don't want to be here anymore.", "language": "en"})
    assert body["risk"] == "high"
    assert "suicidal_ideation" in body["categories"]


async def test_safety_empty_text_rejected(classify):
    with pytest.raises(HTTPException) as exc_info:
        await classify({"text": "   ", "language": "en"})
    assert exc_info.value.status_code == 400


@pytest.mark.parametrize("text", ["ja", "Yes.", "yes, exactly", "Nein!", "ja genau"])
async def test_bare_answer_skips_the_model(classify, fake_llm, text):
    """A plain yes / no is never a risk signal: "Hat er dich gewürgt?" → "ja"
    came back high and showed the helpline instead of a reply (2026-10-14)."""
    body = await classify(
        {
            "text": text,
            "language": "de",
            "stage": "recording",
            "context": "Hat er dich gewürgt?",
        }
    )
    assert body["risk"] == "none"
    assert fake_llm.calls == []


@pytest.mark.parametrize("text", ["8", "yes he did", "yes yes yes yes", "ja, ich will sterben"])
async def test_other_short_replies_reach_the_model(classify, fake_llm, text):
    fake_llm.enqueue(json.dumps({"risk": "none", "categories": [], "confidence": 0.9}))
    await classify({"text": text, "language": "en"})
    assert len(fake_llm.calls) == 1


@pytest.mark.parametrize(
    ("stage", "labelled"),
    [("recording", True), ("rewriting", True), ("summary", True), ("final", False)],
)
async def test_context_marked_as_about_the_dream(classify, fake_llm, stage, labelled):
    fake_llm.enqueue(json.dumps({"risk": "none", "categories": [], "confidence": 0.9}))
    await classify(
        {
            "text": "he did it again",
            "language": "en",
            "stage": stage,
            "context": "What happens next?",
        }
    )
    user_turn = fake_llm.calls[0]["messages"][1]["content"]
    assert ("(about the user's dream)" in user_turn) is labelled
    assert "What happens next?" in user_turn
