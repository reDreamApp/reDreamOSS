"""parse_json_object: the JSON object out of a model answer.

Generic endpoints wrap JSON in code fences or prose, with or without JSON mode,
and some leave the model's reasoning in the content. The crisis classifier and
the summary extraction read their answers through it; the crisis check stays
fail-closed: no single object, no verdict (it raises and the backend answers
502). An example object before the answer must never win over it.

The balanced-brace scan alone finds an object inside a fence; removing the
opening fence marker is what lets a fenced JSON array be rejected like a bare
one.
"""

from __future__ import annotations

import json

import pytest

from redream_ai import summary_structured
from redream_ai.core import agent as core_agent
from redream_ai.llm.fake_client import FakeLlmClient
from redream_ai.llm.json_parse import parse_json_object
from redream_ai.models import Language, SafetyCategory, SafetyRisk
from redream_ai.safety import classify

_HIGH = {"risk": "high", "categories": ["suicidal_ideation"], "confidence": 0.9}
_HIGH_JSON = json.dumps(_HIGH)


def _fenced(payload: str) -> str:
    return f"```json\n{payload}\n```"


def _prose(payload: str) -> str:
    return f"Here is the classification:\n{payload}\nLet me know if you need anything else."


@pytest.mark.parametrize(
    "text",
    [
        _HIGH_JSON,
        _fenced(_HIGH_JSON),
        f"```\n{_HIGH_JSON}\n```",
        _prose(_HIGH_JSON),
        f"Sure!\n{_fenced(_HIGH_JSON)}\nDone.",
        f"  \n{_HIGH_JSON}\n  ",
        f"```{_HIGH_JSON}```",
        f"```json {_HIGH_JSON}```",
        f"```json {_HIGH_JSON}\n```",
        f'<think>Not {{"risk": "none"}}: they want to die.</think>\n{_HIGH_JSON}',
        f'The format is {{"risk": "none"}}.</think>\n{_fenced(_HIGH_JSON)}',
    ],
    ids=[
        "bare",
        "fenced-json",
        "fenced",
        "prose",
        "prose-and-fence",
        "whitespace",
        "one-line-fence",
        "one-line-fence-json",
        "one-line-fence-json-closed-below",
        "after-reasoning",
        "after-reasoning-closing-tag-only",
    ],
)
def test_wrapped_object_is_found(text):
    assert parse_json_object(text) == _HIGH


def test_braces_inside_strings_do_not_end_the_object():
    text = 'Result: {"title": "A {strange} door", "note": "a \\" and a } inside"} end'
    assert parse_json_object(text) == {"title": "A {strange} door", "note": 'a " and a } inside'}


def test_nested_objects_stay_inside_the_object():
    assert parse_json_object('Result: {"a": {"b": 1}, "c": [{"d": 2}]}') == {
        "a": {"b": 1},
        "c": [{"d": 2}],
    }


def test_braces_in_prose_before_the_object_are_skipped():
    assert parse_json_object('Format {risk}: {"risk": "high"}') == {"risk": "high"}


@pytest.mark.parametrize(
    "text",
    [
        "",
        "I can't help with that.",
        "[1, 2]",
        "{not json}",
        "```json\n```",
        # Truncated: unclosed, so no object at all ...
        '{"risk": "high", "categories": ["self_harm"]',
        # ... and never the inside of it: a cut-off "high" must not turn into
        # the "none" nested in it.
        '{"risk": "high", "detail": {"risk": "none"}',
        # An array is not the object, not even its first element ...
        '[{"risk": "none"}, {"risk": "high"}]',
        '[{"risk": "none"}] is my answer.',
        _fenced('[{"risk": "none"}]'),
        # ... and with two objects neither is the answer: an example (in prose
        # or in reasoning a server left in the content) must not win.
        'Example {"risk": "none"} ... actual {"risk": "high"}',
        '{"risk": "none"}\n{"risk": "none"}',
        '<think>The format is {"risk": "none"}. But this is suicidal ideation.\n{"risk": "high"}',
    ],
    ids=[
        "empty",
        "prose",
        "array-of-numbers",
        "not-json",
        "empty-fence",
        "truncated",
        "truncated-nested",
        "array",
        "array-and-prose",
        "fenced-array",
        "example-then-answer",
        "two-objects",
        "unclosed-reasoning",
    ],
)
def test_no_single_object_raises_value_error(text):
    with pytest.raises(ValueError):
        parse_json_object(text)


# --- crisis classifier -----------------------------------------------------------


async def _classify(llm: FakeLlmClient, *, json_mode: bool = True):
    return await classify(
        llm=llm,
        model="m-small",
        text="I don't want to be here anymore.",
        language=Language.EN,
        json_mode=json_mode,
    )


def _after_reasoning(payload: str) -> str:
    return f'<think>The format is {{"risk": "none"}}. But this is ideation.</think>\n{payload}'


@pytest.mark.parametrize(
    "wrap", [_fenced, _prose, _after_reasoning], ids=["fenced", "prose", "after-reasoning"]
)
async def test_crisis_classifier_reads_wrapped_json(fake_llm, wrap):
    fake_llm.enqueue(wrap(_HIGH_JSON))

    resp = await _classify(fake_llm, json_mode=False)

    assert resp.risk == SafetyRisk.HIGH
    assert resp.categories == [SafetyCategory.SUICIDAL_IDEATION]
    assert resp.confidence == 0.9


@pytest.mark.parametrize(
    "answer",
    [
        "I'm not able to classify this.",
        "",
        '{"risk": "high"',
        _fenced('{"categories": []}'),
        _prose('{"risk": "maybe"}'),
        # Earlier, json.loads rejected these; the "none" in them must not
        # become the verdict now that wrapped JSON parses.
        '[{"risk": "none"}, {"risk": "high"}]',
        'Example {"risk": "none"} ... actual {"risk": "high"}',
        '<think>The format is {"risk": "none"}. But this is suicidal ideation.\n{"risk": "high"}',
    ],
    ids=[
        "prose",
        "empty",
        "truncated",
        "no-risk",
        "unknown-risk",
        "array",
        "example-then-answer",
        "unclosed-reasoning",
    ],
)
async def test_crisis_classifier_fails_closed_on_an_unusable_answer(fake_llm, answer):
    fake_llm.enqueue(answer)
    with pytest.raises((ValueError, KeyError)):
        await _classify(fake_llm, json_mode=False)


# --- summary extraction ----------------------------------------------------------

_SUMMARY = {
    "title": "The Forest",
    "rewritten_summary": "You walk calmly through the forest and find your way home.",
    "original_summary": "You are being chased through a dark forest.",
}


class _ReplyAgent:
    """Response-agent stub answering every generate call with one reply."""

    def __init__(self, reply):
        self._reply = reply
        self.calls = []

    async def generate(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        return self._reply, {"input": 1, "output": 1, "total": 2}, None


@pytest.mark.parametrize("wrap", [_fenced, _prose], ids=["fenced", "prose"])
async def test_summary_extraction_reads_wrapped_json(monkeypatch, wrap):
    monkeypatch.setattr(core_agent, "response_agent", _ReplyAgent(wrap(json.dumps(_SUMMARY))))

    out = await summary_structured.extract_summary_structured("Title: The Forest", Language.EN)

    assert out is not None
    assert out.title == _SUMMARY["title"]
    assert out.rewritten_summary == _SUMMARY["rewritten_summary"]
    assert out.original_summary == out.dream_summary == _SUMMARY["original_summary"]


async def test_summary_extraction_without_an_object_is_none(monkeypatch):
    monkeypatch.setattr(core_agent, "response_agent", _ReplyAgent("Title: The Forest, sorry."))
    assert await summary_structured.extract_summary_structured("x", Language.EN) is None
