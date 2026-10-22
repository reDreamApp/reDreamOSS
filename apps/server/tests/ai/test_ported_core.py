"""Invariant + routing tests for the ported IRT core (§6/§7 of the integration brief).

Pins the load-bearing constants (model strings, base URLs, prompt structure) and
the stage-routing logic. No network: `determine_stage_async` is exercised with a
stubbed routing agent.
"""

from __future__ import annotations

import inspect
import math

from redream_ai.clinical_safety import critic
from redream_ai.core import irt_app
from redream_ai.core.agent import MODELS, Agent, LogprobsResult, TokenLogprob
from redream_ai.core.models import Conversation
from redream_ai.core.prompts import (
    ROUTING_PROMPT,
    SYSTEM_PROMPT_TEMPLATES_DE,
    SYSTEM_PROMPT_TEMPLATES_EN,
)

_STAGE_KEYS = {"recording", "rewriting", "summary", "rehearsal", "final"}


# --- pinned constants (a model-id typo must fail CI) ---------------------------

def test_active_model_strings_pinned():
    assert MODELS["MISTRAL_MEDIUM"].name == "mistral-medium-3.5-128b"
    assert MODELS["MISTRAL_MEDIUM"].provider == "scaleway"
    assert MODELS["MISTRAL_SMALL"].name == "mistral-small-3.2-24b-instruct-2506"
    assert MODELS["MISTRAL_SMALL"].provider == "scaleway"
    # Response fallback: deliberately a FLOATING alias (2026-07-02 ADR 0010
    # exception) — tracks Mistral's current medium, matching the pinned primary.
    assert MODELS["MISTRAL_MEDIUM_DIRECT"].name == "mistral-medium-latest"
    assert MODELS["MISTRAL_MEDIUM_DIRECT"].provider == "mistral"
    assert MODELS["MINISTRAL_8B"].name == "ministral-8b-2512"
    assert MODELS["MINISTRAL_8B"].provider == "mistral"
    assert MODELS["MISTRAL_SMALL_DIRECT"].name == "mistral-small-2603"
    assert MODELS["MISTRAL_SMALL_DIRECT"].provider == "mistral"


def test_provider_base_urls_exact():
    src = inspect.getsource(Agent._build_client)
    assert "https://api.scaleway.ai/v1" in src
    assert "https://api.mistral.ai/v1" in src


# --- prompt invariants ---------------------------------------------------------

def test_de_en_template_parity():
    assert set(SYSTEM_PROMPT_TEMPLATES_DE) == _STAGE_KEYS
    assert set(SYSTEM_PROMPT_TEMPLATES_EN) == _STAGE_KEYS


def test_routing_prompt_excludes_rehearsal_value():
    # 'rehearsal' appears only in the therapy name, never as a routed stage value.
    assert ROUTING_PROMPT.lower().count("rehearsal") == 1
    values_line = ROUTING_PROMPT.split("these values:", 1)[1].split("\n", 1)[0]
    assert "rehearsal" not in values_line.lower()


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


def test_crisis_line_present_in_scope_boundary():
    from redream_ai.core.prompts import SCOPE_BOUNDARY_DE, SCOPE_BOUNDARY_EN
    for boundary in (SCOPE_BOUNDARY_DE, SCOPE_BOUNDARY_EN):
        assert "0800 111 0 111" in boundary
        assert "112" in boundary


# --- §5 confidence gate (regression trap) --------------------------------------

def test_confidence_threshold_is_zero():
    assert critic.CONFIDENCE_THRESHOLD == 0.0


def test_logprobs_confidence_semantics():
    assert LogprobsResult().confidence == 0.0  # no tokens → 0.0
    r = LogprobsResult(tokens=[TokenLogprob("ok", -0.2)])
    assert abs(r.confidence - math.exp(-0.2)) < 1e-9


# --- stage routing logic (stubbed router, no network) --------------------------

class _StubAgent:
    def __init__(self, stage_str):
        self._stage = stage_str

    async def generate(self, prompt, **kwargs):
        return self._stage, {"input": 1, "output": 1, "total": 2}, None


async def test_first_turn_defaults_to_recording(monkeypatch):
    # garbage routing output on turn 1 (empty stages) must default to RECORDING, not IndexError
    monkeypatch.setattr(irt_app, "routing_agent", _StubAgent("not-a-real-stage"))
    conv = Conversation(session_id="s", language="en")
    assert await irt_app.determine_stage_async("hi", conv) == "recording"


async def test_rehearsal_is_remapped_to_final(monkeypatch):
    monkeypatch.setattr(irt_app, "routing_agent", _StubAgent("rehearsal"))
    conv = Conversation(session_id="s", language="en", stages=["recording", "rewriting", "summary"])
    # rehearsal is not routed in the app build → final (summary already present)
    assert await irt_app.determine_stage_async("no questions", conv) == "final"


async def test_final_requires_summary_first(monkeypatch):
    monkeypatch.setattr(irt_app, "routing_agent", _StubAgent("final"))
    conv = Conversation(session_id="s", language="en", stages=["recording", "rewriting"])
    # no summary yet → guard redirects to summary
    assert await irt_app.determine_stage_async("done", conv) == "summary"


def _conversation(*, user_messages: int, stages: list[str]) -> Conversation:
    conv = Conversation(session_id="s", language="en", stages=list(stages))
    for i in range(user_messages):
        conv.add_message(f"message {i}", "user")
    return conv


async def test_rewriting_cannot_start_on_the_first_message(monkeypatch):
    monkeypatch.setattr(irt_app, "routing_agent", _StubAgent("rewriting"))
    conv = _conversation(user_messages=1, stages=[])
    # a dream plus "let's rewrite it" in one message still gets one recording reply
    assert await irt_app.determine_stage_async("dream, rewrite now", conv) == "recording"


async def test_rewriting_starts_from_the_second_message(monkeypatch):
    monkeypatch.setattr(irt_app, "routing_agent", _StubAgent("rewriting"))
    conv = _conversation(user_messages=2, stages=["recording"])
    assert await irt_app.determine_stage_async("yes", conv) == "rewriting"


async def test_summary_requires_a_rewriting_turn(monkeypatch):
    monkeypatch.setattr(irt_app, "routing_agent", _StubAgent("summary"))
    conv = _conversation(user_messages=3, stages=["recording", "recording"])
    assert await irt_app.determine_stage_async("summarize", conv) == "rewriting"


async def test_no_stage_is_skipped_on_the_first_message(monkeypatch):
    monkeypatch.setattr(irt_app, "routing_agent", _StubAgent("final"))
    conv = _conversation(user_messages=1, stages=[])
    # final -> summary -> rewriting -> recording: every guard applies in turn
    assert await irt_app.determine_stage_async("done", conv) == "recording"


async def test_a_session_never_goes_back_to_recording(monkeypatch):
    monkeypatch.setattr(irt_app, "routing_agent", _StubAgent("recording"))
    conv = _conversation(user_messages=4, stages=["recording", "recording", "rewriting"])
    assert await irt_app.determine_stage_async("I forgot a part", conv) == "rewriting"
    conv = _conversation(user_messages=6, stages=["recording", "rewriting", "summary"])
    assert await irt_app.determine_stage_async("hm", conv) == "summary"


async def test_recording_continues_while_the_router_says_so(monkeypatch):
    monkeypatch.setattr(irt_app, "routing_agent", _StubAgent("recording"))
    conv = _conversation(user_messages=3, stages=["recording", "recording"])
    assert await irt_app.determine_stage_async("and then I fell", conv) == "recording"
