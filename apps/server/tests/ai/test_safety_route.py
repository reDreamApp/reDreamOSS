"""`safety_classify` — crisis pre-classifier (ADR 0005).

This path is independent of the ported IRT core and must keep working after the
core replacement. FakeLlmClients are passed in as the two providers (no network).
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
from redream_ai.models import SafetyRequest


@pytest.fixture
def fallback_llm() -> FakeLlmClient:
    return FakeLlmClient()


@pytest.fixture
def classify(fake_llm, fallback_llm):
    """`fake_llm` stands in for Scaleway, `fallback_llm` for direct Mistral.
    Either can be swapped per call; returns the response as wire JSON."""

    async def run(
        body: dict[str, Any],
        *,
        llm: LlmClient | None = None,
        fallback: LlmClient | None = None,
    ) -> dict[str, Any]:
        resp = await safety_classify(
            SafetyRequest.model_validate(body),
            llm=llm or fake_llm,
            fallback_llm=fallback or fallback_llm,
        )
        return resp.model_dump(mode="json")

    return run


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


async def test_scaleway_answers_first(classify, fake_llm, fallback_llm):
    fake_llm.enqueue(_NONE)
    await classify({"text": "I dreamt of a forest.", "language": "en"})
    assert [c["model"] for c in fake_llm.calls] == [settings.scaleway_safety_model]
    assert fallback_llm.calls == []


@pytest.mark.parametrize("primary_reply", [None, "Sorry, I cannot help with that."])
async def test_mistral_answers_when_scaleway_fails(classify, fake_llm, fallback_llm, primary_reply):
    """An outage and an answer that is not the JSON both go to the fallback:
    the backend blocks the chat turn when this check fails."""
    primary = _DownLlm() if primary_reply is None else fake_llm
    if primary_reply is not None:
        fake_llm.enqueue(primary_reply)
    fallback_llm.enqueue(
        json.dumps({"risk": "high", "categories": ["suicidal_ideation"], "confidence": 0.9})
    )
    body = await classify(
        {"text": "I don't want to be here anymore.", "language": "en"}, llm=primary
    )
    assert body["risk"] == "high"
    assert [c["model"] for c in fallback_llm.calls] == [settings.mistral_safety_model]


async def test_both_providers_down_raises_unavailable(classify):
    with pytest.raises(LlmUnavailableError):
        await classify(
            {"text": "I dreamt of a forest.", "language": "en"},
            llm=_DownLlm(),
            fallback=_DownLlm(),
        )


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
