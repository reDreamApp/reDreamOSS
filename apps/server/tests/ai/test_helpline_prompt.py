"""HELPLINE_PROMPT_EN / HELPLINE_PROMPT_DE and the shared intro turn.

Pins the neutral defaults and their startup checks, that the reference is put
into the scope boundary when a prompt is built (not at import) on both the
plain and the streaming response path, that no country's crisis numbers are
left in the prompts, and that both paths open the history with the same intro.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from redream_ai.config import (
    HELPLINE_PROMPT_DE_DEFAULT,
    HELPLINE_PROMPT_EN_DEFAULT,
    HELPLINE_PROMPT_MAX_LEN,
    Settings,
    settings,
)
from redream_ai.core import irt_app
from redream_ai.core import prompts as prompts_mod
from redream_ai.core.models import ChatInput, Conversation
from redream_ai.core.prompts import (
    HELPLINE_REFERENCE,
    INTRO_MESSAGE_DE,
    INTRO_MESSAGE_EN,
    SCOPE_BOUNDARY_DE,
    SCOPE_BOUNDARY_EN,
)

_SRC = Path(irt_app.__file__).resolve().parents[2]  # .../src
_PHONE_112 = re.compile(r"(?<!\d)112(?!\d)")


class _CaptureAgent:
    """Response-agent stub recording every generate/generate_stream call."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def generate(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        return "ok", {"input": 1, "output": 1, "total": 2}, None

    async def generate_stream(self, prompt, system_prompt=None):
        self.calls.append({"prompt": prompt, "system_prompt": system_prompt})
        yield "ok", None, None
        yield "", {"input": 1, "output": 1, "total": 2}, None


async def _plain_system_prompt(monkeypatch, lang: str) -> str:
    stub = _CaptureAgent()
    monkeypatch.setattr(irt_app, "response_agent", stub)
    await irt_app.get_response_async("recording", "hi", Conversation(session_id="s", language=lang))
    return str(stub.calls[0]["system_prompt"])


async def _stream_system_prompt(monkeypatch, lang: str) -> str:
    stub = _CaptureAgent()
    monkeypatch.setattr(irt_app, "response_agent", stub)
    conv = Conversation(session_id="s", language=lang)
    _ = [item async for item in irt_app.get_response_stream_async("recording", "p", conv, lang)]
    return str(stub.calls[0]["system_prompt"])


_PATHS = [
    pytest.param(_plain_system_prompt, id="plain"),
    pytest.param(_stream_system_prompt, id="stream"),
]


# --- settings ------------------------------------------------------------------


def test_defaults_when_unset(monkeypatch):
    monkeypatch.delenv("HELPLINE_PROMPT_EN", raising=False)
    monkeypatch.delenv("HELPLINE_PROMPT_DE", raising=False)
    s = Settings(_env_file=None)
    assert s.helpline_prompt_en == HELPLINE_PROMPT_EN_DEFAULT
    assert s.helpline_prompt_de == HELPLINE_PROMPT_DE_DEFAULT


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_means_default(monkeypatch, blank):
    monkeypatch.setenv("HELPLINE_PROMPT_EN", blank)
    monkeypatch.setenv("HELPLINE_PROMPT_DE", blank)
    s = Settings(_env_file=None)
    assert s.helpline_prompt_en == HELPLINE_PROMPT_EN_DEFAULT
    assert s.helpline_prompt_de == HELPLINE_PROMPT_DE_DEFAULT


def test_custom_reference_is_stripped(monkeypatch):
    monkeypatch.setenv("HELPLINE_PROMPT_EN", "  Samaritans (116 123)\n")
    assert Settings(_env_file=None).helpline_prompt_en == "Samaritans (116 123)"


@pytest.mark.parametrize("var", ["HELPLINE_PROMPT_EN", "HELPLINE_PROMPT_DE"])
def test_too_long_reference_stops_startup(monkeypatch, var):
    monkeypatch.setenv(var, "x" * (HELPLINE_PROMPT_MAX_LEN + 1))
    with pytest.raises(ValidationError, match=var):
        Settings(_env_file=None)


def test_reference_at_the_limit_is_accepted(monkeypatch):
    monkeypatch.setenv("HELPLINE_PROMPT_DE", "x" * HELPLINE_PROMPT_MAX_LEN)
    assert len(Settings(_env_file=None).helpline_prompt_de) == HELPLINE_PROMPT_MAX_LEN


@pytest.mark.parametrize("var", ["HELPLINE_PROMPT_EN", "HELPLINE_PROMPT_DE"])
@pytest.mark.parametrize("value", ["line one\nline two", "line one\rline two"])
def test_multi_line_reference_stops_startup(monkeypatch, var, value):
    monkeypatch.setenv(var, value)
    with pytest.raises(ValidationError, match=f"{var} must be a single line"):
        Settings(_env_file=None)


# --- composed at call time ------------------------------------------------------


def test_constants_keep_one_placeholder_each():
    assert SCOPE_BOUNDARY_EN.count(HELPLINE_REFERENCE) == 1
    assert SCOPE_BOUNDARY_DE.count(HELPLINE_REFERENCE) == 1


@pytest.mark.parametrize(
    ("is_english", "default"),
    [(True, HELPLINE_PROMPT_EN_DEFAULT), (False, HELPLINE_PROMPT_DE_DEFAULT)],
    ids=["en", "de"],
)
def test_composed_default(is_english, default):
    composed = irt_app.scope_boundary(is_english)
    assert f"({default})" in composed
    assert HELPLINE_REFERENCE not in composed


@pytest.mark.parametrize("path", _PATHS)
@pytest.mark.parametrize("lang", ["en", "de"])
async def test_setting_changed_after_import_reaches_next_prompt(monkeypatch, path, lang):
    """The reference is read when the prompt is built: a value set after
    import shows up in the next system prompt, braces and all (no str.format)."""
    field = f"helpline_prompt_{lang}"
    custom = f"{{custom}} {lang} line, call 116 123"
    before = await path(monkeypatch, lang)
    assert getattr(settings, field) in before

    monkeypatch.setattr(settings, field, custom)
    after = await path(monkeypatch, lang)

    assert f"({custom})" in after
    assert HELPLINE_REFERENCE not in after
    other = HELPLINE_PROMPT_DE_DEFAULT if lang == "en" else HELPLINE_PROMPT_EN_DEFAULT
    assert other not in after


# --- no German crisis numbers left ------------------------------------------------


def _assert_no_german_crisis_line(text: str) -> None:
    assert "0800" not in text
    assert "telefonseelsorge" not in text.lower()
    assert not _PHONE_112.search(text)


def test_prompts_module_and_composed_defaults_have_no_german_crisis_line():
    _assert_no_german_crisis_line(Path(prompts_mod.__file__).read_text(encoding="utf-8"))
    _assert_no_german_crisis_line(irt_app.scope_boundary(True))
    _assert_no_german_crisis_line(irt_app.scope_boundary(False))
    _assert_no_german_crisis_line(HELPLINE_PROMPT_EN_DEFAULT)
    _assert_no_german_crisis_line(HELPLINE_PROMPT_DE_DEFAULT)


def test_no_german_crisis_line_anywhere_in_src():
    for path in _SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        assert "0800 111" not in text, path
        assert "telefonseelsorge" not in text, path


# --- the intro turn -------------------------------------------------------------


_EFFECT_WORDS = ("positive", "empowering", "work through", "bewältigen", "positivere")


@pytest.mark.parametrize("intro", [INTRO_MESSAGE_EN, INTRO_MESSAGE_DE], ids=["en", "de"])
def test_intro_shape_and_wording(intro):
    assert intro.startswith("AI: ")
    # The separator is a literal backslash-n (as the history string has always had).
    assert intro.count("\\n") == 1
    assert "\n" not in intro
    for word in _EFFECT_WORDS:
        assert word not in intro.lower()


@pytest.mark.parametrize(
    ("lang", "intro"), [("en", INTRO_MESSAGE_EN), ("de", INTRO_MESSAGE_DE)], ids=["en", "de"]
)
async def test_intro_is_the_same_on_both_paths(monkeypatch, lang, intro):
    """process_chat_message_stream and get_response_async open the history
    with the same intro turn."""
    stub = _CaptureAgent()
    monkeypatch.setattr(irt_app, "response_agent", stub)

    async def fake_determine_stage(user_input, conversation):
        conversation.stages.append("final")  # no stage note, no LLM call
        return "final"

    monkeypatch.setattr(irt_app, "determine_stage_async", fake_determine_stage)
    conv = Conversation(session_id="s", language=lang)
    chat_input = ChatInput(session_id="s", message="I was chased.", language_override=lang)
    _ = [frame async for frame in irt_app.process_chat_message_stream(chat_input, conv)]
    stream_prompt = stub.calls[0]["prompt"]

    plain_conv = Conversation(session_id="s", language=lang)
    plain_conv.add_message("I was chased.", "user")
    await irt_app.get_response_async("final", "I was chased.", plain_conv)
    plain_prompt = stub.calls[1]["prompt"]

    opening = "\n\nConversation history:\n" + intro + "\\n" + "User: I was chased."
    assert stream_prompt == opening
    assert plain_prompt == opening
