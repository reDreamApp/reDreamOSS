"""Invariant + routing tests for the ported IRT core (§6/§7 of the integration brief).

Pins the load-bearing constants (prompt structure, confidence threshold) and the
stage-routing logic; the model and endpoint wiring is in test_core_agent.py. No
network: `determine_stage_async` is exercised with a stubbed routing agent.
"""

from __future__ import annotations

import math

import pytest

from redream_ai.clinical_safety import critic
from redream_ai.core import irt_app
from redream_ai.core.agent import LogprobsResult, TokenLogprob
from redream_ai.core.models import Conversation
from redream_ai.core.prompts import (
    ROUTING_PROMPTS,
    SUMMARY_REQUEST_DE,
    SUMMARY_REQUEST_EN,
    SUMMARY_REVISION_REQUEST_DE,
    SUMMARY_REVISION_REQUEST_EN,
    SYSTEM_PROMPT_TEMPLATES_DE,
    SYSTEM_PROMPT_TEMPLATES_EN,
)

_STAGE_KEYS = {"recording", "rewriting", "summary", "rehearsal", "final"}


# --- prompt invariants ---------------------------------------------------------

def test_de_en_template_parity():
    assert set(SYSTEM_PROMPT_TEMPLATES_DE) == _STAGE_KEYS
    assert set(SYSTEM_PROMPT_TEMPLATES_EN) == _STAGE_KEYS


def test_routing_checks_cover_the_routed_steps():
    # One yes/no question per step that can move on; final has none, and the
    # router hears no rehearsal step and no therapy name.
    assert set(ROUTING_PROMPTS) == {"recording", "rewriting", "summary"}
    for prompt in ROUTING_PROMPTS.values():
        assert prompt.endswith("Answer only YES or NO.")
        assert "rehearsal" not in prompt.lower()
        assert "therap" not in prompt.lower()


def test_summary_last_line_exact():
    assert "Are you satisfied with the generated summary?" in SYSTEM_PROMPT_TEMPLATES_EN["summary"]
    assert (
        "Bist du mit der generierten Zusammenfassung zufrieden?"
        in SYSTEM_PROMPT_TEMPLATES_DE["summary"]
    )


def test_summary_labels_rewritten_before_original():
    en = SYSTEM_PROMPT_TEMPLATES_EN["summary"]
    assert en.index("Rewritten Dream:") < en.index("Original Dream:")
    de = SYSTEM_PROMPT_TEMPLATES_DE["summary"]
    assert de.index("Umgeschriebener Traum:") < de.index("Ursprünglicher Traum:")


def test_help_reference_present_in_scope_boundary():
    # The distress clause names where to find help: one placeholder per language,
    # filled with HELPLINE_PROMPT_EN / HELPLINE_PROMPT_DE when the prompt is built.
    from redream_ai.config import settings
    from redream_ai.core.prompts import HELPLINE_REFERENCE, SCOPE_BOUNDARY_DE, SCOPE_BOUNDARY_EN

    for boundary in (SCOPE_BOUNDARY_DE, SCOPE_BOUNDARY_EN):
        assert boundary.count(HELPLINE_REFERENCE) == 1
    assert settings.helpline_prompt_en in irt_app.scope_boundary(True)
    assert settings.helpline_prompt_de in irt_app.scope_boundary(False)


# --- §5 confidence gate (regression trap) --------------------------------------

def test_confidence_threshold_is_zero():
    assert critic.CONFIDENCE_THRESHOLD == 0.0


def test_logprobs_confidence_semantics():
    assert LogprobsResult().confidence == 0.0  # no tokens → 0.0
    r = LogprobsResult(tokens=[TokenLogprob("ok", -0.2)])
    assert abs(r.confidence - math.exp(-0.2)) < 1e-9


# --- stage routing logic (stubbed router, no network) --------------------------

class _StubAgent:
    """Routing agent stub: answers every step check with `answer`."""

    def __init__(self, answer):
        self._answer = answer
        self.system_prompts = []

    async def generate(self, prompt, system_prompt=None, **kwargs):
        self.system_prompts.append(system_prompt)
        return self._answer, {"input": 1, "output": 1, "total": 2}, None


def _conversation(*, user_messages: int, stages: list[str]) -> Conversation:
    conv = Conversation(session_id="s", language="en", stages=list(stages))
    for i in range(user_messages):
        conv.add_message(f"message {i}", "user")
    return conv


async def test_the_first_message_stays_in_recording_without_a_check(monkeypatch):
    router = _StubAgent("YES")
    monkeypatch.setattr(irt_app, "routing_agent", router)
    conv = _conversation(user_messages=1, stages=[])
    # a dream plus "let's rewrite it" in one message still gets one recording reply
    assert await irt_app.determine_stage_async("dream, rewrite now", conv) == "recording"
    assert router.system_prompts == []


@pytest.mark.parametrize(
    ("current", "answer", "expected"),
    [
        ("recording", "YES", "rewriting"),
        ("recording", "NO", "recording"),
        ("rewriting", "YES", "summary"),
        ("rewriting", "no.", "rewriting"),
        ("summary", "Yes", "final"),
        ("summary", "NO", "summary"),
        ("recording", "maybe", "recording"),  # anything but YES stays
        ("rewriting", "", "rewriting"),
    ],
)
async def test_each_step_asks_its_own_question(monkeypatch, current, answer, expected):
    router = _StubAgent(answer)
    monkeypatch.setattr(irt_app, "routing_agent", router)
    stages = {"recording": ["recording"], "rewriting": ["recording", "rewriting"],
              "summary": ["recording", "rewriting", "summary"]}[current]
    conv = _conversation(user_messages=len(stages) + 1, stages=stages)
    assert await irt_app.determine_stage_async("…", conv) == expected
    assert router.system_prompts == [ROUTING_PROMPTS[current]]


async def test_final_stays_final_without_a_check(monkeypatch):
    router = _StubAgent("NO")
    monkeypatch.setattr(irt_app, "routing_agent", router)
    for last in ("final", "rehearsal"):  # rehearsal: website build only
        conv = _conversation(user_messages=5, stages=["recording", "rewriting", "summary", last])
        assert await irt_app.determine_stage_async("thanks", conv) == "final"
    assert router.system_prompts == []


async def test_a_pre_port_intensity_turn_counts_as_rewriting(monkeypatch):
    router = _StubAgent("NO")
    monkeypatch.setattr(irt_app, "routing_agent", router)
    conv = _conversation(user_messages=3, stages=["recording", "intensity"])
    assert await irt_app.determine_stage_async("ok", conv) == "rewriting"
    assert router.system_prompts == [ROUTING_PROMPTS["rewriting"]]


def test_the_order_guard_still_holds():
    """_keep_stage_order stays the last word, whatever the router says."""
    conv = _conversation(user_messages=3, stages=["recording", "recording"])
    assert irt_app._keep_stage_order(irt_app.Stage.FINAL, conv) == irt_app.Stage.REWRITING
    conv = _conversation(user_messages=4, stages=["recording", "rewriting"])
    assert irt_app._keep_stage_order(irt_app.Stage.RECORDING, conv) == irt_app.Stage.REWRITING
    conv = _conversation(user_messages=1, stages=[])
    assert irt_app._keep_stage_order(irt_app.Stage.FINAL, conv) == irt_app.Stage.RECORDING


# --- what the models are sent (2026-10-25 restructure) -----------------------------

class _RecordingAgent:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    async def generate(self, prompt, system_prompt=None, **kwargs):
        self.calls.append({"prompt": prompt, "system_prompt": system_prompt})
        return self.reply, {"input": 1, "output": 1, "total": 2}, None


async def test_the_router_reads_the_whole_conversation(monkeypatch):
    router = _RecordingAgent("YES")
    monkeypatch.setattr(irt_app, "routing_agent", router)
    conv = Conversation(session_id="s", language="en")
    conv.add_message("A wolf chases me.", "user")
    conv.add_message("Would you like to start rewriting it now?", "assistant", "recording")
    conv.add_message("Yes", "user")
    conv.stages = ["recording"]

    assert await irt_app.determine_stage_async("Yes", conv) == "rewriting"

    assert router.calls[0]["system_prompt"] == ROUTING_PROMPTS["recording"]
    assert router.calls[0]["prompt"] == (
        "<conversation>\nUser: A wolf chases me.\n"
        "Assistant: Would you like to start rewriting it now?\nUser: Yes\n</conversation>"
    )


async def test_the_reply_model_gets_the_chat_as_turns(monkeypatch):
    responder = _RecordingAgent("ok")
    monkeypatch.setattr(irt_app, "response_agent", responder)
    conv = Conversation(session_id="s", language="en")
    conv.add_message("I want to die.", "user")  # answered by the helpline only
    conv.add_message("A wolf chases me.", "user")
    conv.add_message("That sounds frightening. How does it end?", "assistant", "recording")
    conv.add_message("It catches me.", "user")

    await irt_app.get_response_async("recording", "It catches me.", conv)

    assert responder.calls[0]["prompt"] == [
        {"role": "user", "content": "I want to die.\n\nA wolf chases me."},
        {"role": "assistant", "content": "That sounds frightening. How does it end?"},
        {"role": "user", "content": "It catches me."},
    ]


def test_turns_start_with_the_user():
    conv = Conversation(session_id="s", language="de")
    conv.add_message("Hallo!", "assistant")
    conv.add_message("Hi", "user")
    assert conv.history_as_turns() == [{"role": "user", "content": "Hi"}]


@pytest.mark.parametrize(
    ("lang", "request_text"),
    [("en", SUMMARY_REQUEST_EN), ("de", SUMMARY_REQUEST_DE)],
)
async def test_the_summary_reads_the_chat_as_one_document(monkeypatch, lang, request_text):
    """As chat turns the model went on talking instead of writing the summary."""
    responder = _RecordingAgent("Title: ...")
    monkeypatch.setattr(irt_app, "response_agent", responder)
    conv = Conversation(session_id="s", language=lang)
    conv.add_message("A wolf chases me.", "user")
    conv.add_message("Shall I write the summary?", "assistant", "rewriting")
    conv.add_message("Yes", "user")

    await irt_app.get_response_async("summary", "Yes", conv)

    assert responder.calls[0]["prompt"] == [
        {
            "role": "user",
            "content": "<conversation>\nUser: A wolf chases me.\n"
            "Assistant: Shall I write the summary?\nUser: Yes\n</conversation>\n\n" + request_text,
        }
    ]


@pytest.mark.parametrize(
    ("lang", "request_text"),
    [("en", SUMMARY_REVISION_REQUEST_EN), ("de", SUMMARY_REVISION_REQUEST_DE)],
)
async def test_a_reply_to_a_summary_is_quoted_in_the_request(monkeypatch, lang, request_text):
    """With the plain request the same summary came back whatever the user asked."""
    responder = _RecordingAgent("Title: ...")
    monkeypatch.setattr(irt_app, "response_agent", responder)
    conv = Conversation(session_id="s", language=lang, stages=["rewriting", "summary", "summary"])
    conv.add_message("A wolf chases me.", "user")
    conv.add_message("Title: The wolf", "assistant", "summary")
    conv.add_message("Add that I climb a tree.", "user")

    await irt_app.get_response_async("summary", "Add that I climb a tree.", conv)

    content = responder.calls[0]["prompt"][0]["content"]
    assert content.endswith(request_text.format(message="Add that I climb a tree."))
