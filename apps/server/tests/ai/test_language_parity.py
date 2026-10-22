"""DE/EN parity for the routed IRT flow (brief §6: "DE and EN stay in sync").

The app routes recording → rewriting → summary → final ONLY — 'rehearsal' and
'intensity' are NOT routed stages (rehearsal happens later in the dream
journal; the 0-10 intensity rating lives on SummaryStructured, not in chat).
These tests pin: per-routed-stage template presence in both languages, the
language-selection paths (response, streaming response, nightmare summary),
the ROUTING_PROMPT's allowed stage vocabulary, and the structured summary
extraction for both languages with a fake LLM.
"""

from __future__ import annotations

import json
import re

import pytest

from redream_ai import summary_structured
from redream_ai.core import agent as core_agent
from redream_ai.core import irt_app
from redream_ai.core.models import Conversation
from redream_ai.core.prompts import (
    HELPLINE_REFERENCE,
    NIGHTMARE_SUMMARY_PROMPT_DE,
    NIGHTMARE_SUMMARY_PROMPT_EN,
    ROUTING_PROMPT,
    SYSTEM_PROMPT_TEMPLATES_DE,
    SYSTEM_PROMPT_TEMPLATES_EN,
)
from redream_ai.models import Language

ROUTED_STAGES = ("recording", "rewriting", "summary", "final")

# The scope boundary as composed at call time (HELPLINE_PROMPT_* inserted).
LANG_CASES = [
    pytest.param("de", irt_app.scope_boundary(False), SYSTEM_PROMPT_TEMPLATES_DE, id="de"),
    pytest.param("en", irt_app.scope_boundary(True), SYSTEM_PROMPT_TEMPLATES_EN, id="en"),
]


class _CaptureAgent:
    """Response-agent stub recording every generate/generate_stream call."""

    def __init__(self, reply="ok"):
        self._reply = reply
        self.calls = []

    async def generate(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        return self._reply, {"input": 1, "output": 1, "total": 2}, None

    async def generate_stream(self, prompt, system_prompt=None):
        self.calls.append({"prompt": prompt, "system_prompt": system_prompt})
        yield self._reply, None, None
        yield "", {"input": 1, "output": 1, "total": 2}, None


# --- template presence & routing vocabulary ---------------------------------------


@pytest.mark.parametrize("stage", ROUTED_STAGES)
def test_routed_stage_has_nonempty_template_in_both_languages(stage):
    assert SYSTEM_PROMPT_TEMPLATES_DE[stage].strip()
    assert SYSTEM_PROMPT_TEMPLATES_EN[stage].strip()


def test_routing_prompt_allowed_stages_are_exactly_the_routed_flow():
    # The instruction "respond with exactly one of these values: ..." is the
    # router's whole output vocabulary. It must be exactly the 4 routed stages —
    # no rehearsal, no intensity.
    values_line = ROUTING_PROMPT.split("these values:", 1)[1].split("\n", 1)[0]
    assert set(re.findall(r"[a-z]+", values_line)) == set(ROUTED_STAGES)


def test_intensity_is_not_a_routed_stage_anywhere():
    assert "intensity" not in ROUTING_PROMPT.lower()
    assert "intensity" not in SYSTEM_PROMPT_TEMPLATES_DE
    assert "intensity" not in SYSTEM_PROMPT_TEMPLATES_EN


# --- prompt-selection paths honour the language -----------------------------------


@pytest.mark.parametrize("stage", ROUTED_STAGES)
@pytest.mark.parametrize(("lang", "boundary", "templates"), LANG_CASES)
async def test_response_prompt_selection_honours_language(
    monkeypatch, stage, lang, boundary, templates
):
    stub = _CaptureAgent("Antwort")
    monkeypatch.setattr(irt_app, "response_agent", stub)
    conv = Conversation(session_id="s", language=lang)

    response, _usage = await irt_app.get_response_async(stage, "hi", conv)

    assert response == "Antwort"
    system_prompt = stub.calls[0]["system_prompt"]
    assert system_prompt.startswith(boundary)  # scope boundary prepended, same language
    assert HELPLINE_REFERENCE not in system_prompt
    assert templates[stage] in system_prompt
    other = SYSTEM_PROMPT_TEMPLATES_EN if lang == "de" else SYSTEM_PROMPT_TEMPLATES_DE
    assert other[stage] not in system_prompt


async def test_safety_constraint_is_appended_after_boundary_and_template(monkeypatch):
    stub = _CaptureAgent()
    monkeypatch.setattr(irt_app, "response_agent", stub)
    conv = Conversation(session_id="s", language="en")

    await irt_app.get_response_async(
        "rewriting", "I fight back", conv, safety_constraint="[SAFETY SYSTEM - TEST]"
    )

    assert stub.calls[0]["system_prompt"] == (
        irt_app.scope_boundary(True)
        + "\n\n"
        + SYSTEM_PROMPT_TEMPLATES_EN["rewriting"]
        + "\n\n"
        + "[SAFETY SYSTEM - TEST]"
    )


@pytest.mark.parametrize(("lang", "boundary", "templates"), LANG_CASES)
async def test_stream_prompt_selection_honours_explicit_language(
    monkeypatch, lang, boundary, templates
):
    stub = _CaptureAgent("chunk")
    monkeypatch.setattr(irt_app, "response_agent", stub)
    # Conversation carries the OTHER language: the explicit `language` argument
    # (resolved upstream from override → conversation → "en") must drive selection.
    conv = Conversation(session_id="s", language="en" if lang == "de" else "de")

    out = [
        item
        async for item in irt_app.get_response_stream_async("summary", "full prompt", conv, lang)
    ]

    system_prompt = stub.calls[0]["system_prompt"]
    assert system_prompt.startswith(boundary)
    assert HELPLINE_REFERENCE not in system_prompt
    assert templates["summary"] in system_prompt
    assert out[0] == ("chunk", None)
    assert out[-1] == ("", {"input": 1, "output": 1, "total": 2})


@pytest.mark.parametrize(
    ("lang", "prompt"),
    [
        pytest.param("de", NIGHTMARE_SUMMARY_PROMPT_DE, id="de"),
        pytest.param("en", NIGHTMARE_SUMMARY_PROMPT_EN, id="en"),
    ],
)
async def test_nightmare_summary_prompt_honours_language(monkeypatch, lang, prompt):
    stub = _CaptureAgent("  the summary  ")
    monkeypatch.setattr(irt_app, "response_agent", stub)
    conv = Conversation(session_id="s", language=lang)
    conv.add_message("I was chased through a forest.", "user")

    result = await irt_app.generate_nightmare_summary(conv)

    assert result == "the summary"
    assert stub.calls[0]["system_prompt"] == prompt
    assert "I was chased through a forest." in stub.calls[0]["prompt"]


@pytest.mark.parametrize("reply", ["", "   \n"], ids=["empty", "whitespace"])
async def test_empty_nightmare_summary_is_none(monkeypatch, reply):
    """No content (None from the endpoint arrives as "") is no summary: None
    makes the next turn try again instead of storing an empty one."""
    monkeypatch.setattr(irt_app, "response_agent", _CaptureAgent(reply))
    conv = Conversation(session_id="s", language="en")
    conv.add_message("I was chased through a forest.", "user")

    assert await irt_app.generate_nightmare_summary(conv) is None


# --- structured summary extraction works for both languages (fake LLM) ------------

_PROSE = {
    Language.DE: (
        "Titel: Der Wald\n\n"
        "Umgeschriebener Traum: Du gehst ruhig durch den Wald und findest den Weg nach Hause.\n\n"
        "Ursprünglicher Traum: Du wirst durch einen dunklen Wald gejagt.\n\n"
        "Bist du mit der generierten Zusammenfassung zufrieden?"
    ),
    Language.EN: (
        "Title: The Forest\n\n"
        "Rewritten Dream: You walk calmly through the forest and find your way home.\n\n"
        "Original Dream: You are being chased through a dark forest.\n\n"
        "Are you satisfied with the generated summary?"
    ),
}

_PAYLOAD = {
    Language.DE: {
        "title": "Der Wald",
        "rewritten_summary": "Du gehst ruhig durch den Wald und findest den Weg nach Hause.",
        "original_summary": "Du wirst durch einen dunklen Wald gejagt.",
    },
    Language.EN: {
        "title": "The Forest",
        "rewritten_summary": "You walk calmly through the forest and find your way home.",
        "original_summary": "You are being chased through a dark forest.",
    },
}


@pytest.mark.parametrize("language", [Language.DE, Language.EN], ids=["de", "en"])
async def test_extract_summary_structured_works_per_language(monkeypatch, language):
    stub = _CaptureAgent(json.dumps(_PAYLOAD[language]))
    monkeypatch.setattr(core_agent, "response_agent", stub)

    out = await summary_structured.extract_summary_structured(_PROSE[language], language)

    assert out is not None
    assert out.title == _PAYLOAD[language]["title"]
    assert out.rewritten_summary == _PAYLOAD[language]["rewritten_summary"]
    assert out.original_summary == _PAYLOAD[language]["original_summary"]
    assert out.dream_summary == out.original_summary  # mirrored for the backend
    assert out.intensity is None  # no routed intensity stage in the ported flow
    call = stub.calls[0]
    assert call["prompt"] == _PROSE[language]  # prose passed through verbatim
    assert call["response_format"] == {"type": "json_object"}  # JSON mode requested


async def test_extract_summary_structured_returns_none_on_bad_json(monkeypatch):
    monkeypatch.setattr(core_agent, "response_agent", _CaptureAgent("not json at all"))
    assert await summary_structured.extract_summary_structured("Title: x", Language.EN) is None


async def test_extract_summary_structured_returns_none_without_agent(monkeypatch):
    monkeypatch.setattr(core_agent, "response_agent", None)
    assert await summary_structured.extract_summary_structured("Title: x", Language.DE) is None
