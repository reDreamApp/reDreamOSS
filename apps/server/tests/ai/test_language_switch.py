"""Switching a chat between English and German mid-chat, without network.

The backend asks `language_check` before every turn; on a switch it updates
the chat's language and sends `language_switched=True` with that turn, which
adds a note to the stage prompt. A cheap word check keeps most messages away
from the small model, and any failure keeps the language.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import ValidationError

from redream_ai.api import chat as chatmod
from redream_ai.api import language as language_api
from redream_ai.core import irt_app
from redream_ai.core.models import ChatInput, Conversation
from redream_ai.core.models import ChatResponse as PortedResponse
from redream_ai.core.prompts import (
    HELPLINE_REFERENCE,
    LANGUAGE_CHECK_PROMPT,
    LANGUAGE_SWITCH_NOTE_DE,
    LANGUAGE_SWITCH_NOTE_EN,
    REWRITING_NOTHING_YET_NOTE_DE,
    SCOPE_BOUNDARY_DE,
    SCOPE_BOUNDARY_EN,
    SYSTEM_PROMPT_TEMPLATES_DE,
    SYSTEM_PROMPT_TEMPLATES_EN,
    TALK_GATE_PROMPT,
)
from redream_ai.models import ChatRequest, Language, LanguageCheckRequest


class _Check:
    """Stands in for the routing agent (the small model) on the language check."""

    def __init__(self, answer: str = "OTHER", fail: Exception | None = None, delay: float = 0):
        self.answer = answer
        self.fail = fail
        self.delay = delay
        self.calls: list[str] = []

    async def generate(self, prompt: str, system_prompt: str | None = None, **kwargs: Any) -> Any:
        assert system_prompt == LANGUAGE_CHECK_PROMPT
        self.calls.append(prompt)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise self.fail
        return self.answer, {}, None


@pytest.fixture
def check(monkeypatch: pytest.MonkeyPatch) -> _Check:
    agent = _Check()
    monkeypatch.setattr(irt_app, "routing_agent", agent)
    monkeypatch.setattr(irt_app, "_language_verdicts", {})
    return agent


# --- the word check before the model ---------------------------------------------


@pytest.mark.parametrize(
    ("current", "message"),
    [
        ("en", "kannst du deutsch reden"),
        ("en", "nicht im traum, generell"),
        ("en", "Auf Deutsch bitte"),
        ("en", "Können wir auf Deutschen weitermachen?"),
        ("de", "can we speak english please"),
        ("de", "I had a dream about my brother"),
        ("de", "Lieber auf Englisch"),
        # Contractions are one word, with either apostrophe; "German" in a
        # German chat is an English word.
        ("de", "I don't speak German"),
        ("de", "Sorry I don\u2019t speak German"),
        ("de", "I don't understand"),
        ("de", "No German please"),
        ("en", "Kein Englisch"),
        ("en", "Can you speak Hochdeutsch"),
        # The name alone is enough, in either chat.
        ("de", "German?"),
        ("en", "German?"),
        # Short plain sentences, distress included.
        ("de", "I feel hopeless"),
        ("de", "I need help"),
        ("de", "I woke up screaming"),
        ("en", "Es hat keinen Sinn mehr"),
        ("en", "Mir geht es heute schlecht"),
        ("en", "Niemand versteht mich"),
        ("en", "Es war dunkel und kalt"),
        ("en", "Bitte antworte in meiner Sprache"),
        # One word of the other language and none of the current one.
        ("en", "Ich will sterben"),
        ("en", "Der Traum war schlimm"),
        ("de", "Life is pointless"),
        ("de", "I'm not okay"),
        # Mixed: two different words of the other language, and more of them
        # than of the current one.
        ("en", "Ich weiß nicht, was ich tun soll, it is schwer"),
        ("de", "I think I need a break, bitte"),
    ],
)
def test_these_messages_reach_the_model(current: str, message: str) -> None:
    assert irt_app._may_switch_language(message, current) is True


@pytest.mark.parametrize(
    ("current", "message"),
    [
        ("en", "ja"),
        ("en", "ok"),
        ("en", "I was in Berlin"),
        ("en", "Danke"),
        ("en", "I dreamt my Oma said ich liebe dich to me"),  # a quote in an English sentence
        ("de", "ok"),
        ("de", "nein"),
        ("de", "Ich war in London"),
        ("de", "Ich hatte einen Traum über meinen Bruder"),
        ("en", "I was at the Oktoberfest with my friends"),
        ("en", "The wolf was chasing me in the forest"),
        ("de", "Mein Hund heißt Buddy"),
        ("de", "Ich habe Angst vor dem Wolf"),
        ("de", "I\u2019m scared"),  # two words
        ("de", "Ich habe The Walking Dead gesehen"),  # a title in a German sentence
        ("de", "Ich habe Harry Potter gelesen"),
        # Mixed: two different words of the other language, but no more of
        # them than of the current one.
        ("en", "I think ich bin here"),
        # Mixed: more words of the other language, but only one word repeated.
        ("en", "bitte bitte I'm stuck"),
    ],
)
def test_these_messages_do_not(current: str, message: str) -> None:
    assert irt_app._may_switch_language(message, current) is False


@pytest.mark.parametrize("message", ["ja", "ok", "I was in Berlin", "Danke"])
async def test_a_message_that_fails_the_word_check_costs_no_call(
    check: _Check, message: str
) -> None:
    assert await irt_app.resolve_language_switch(message, "en") is None
    assert check.calls == []


async def test_an_empty_message_or_an_unknown_language_is_kept(check: _Check) -> None:
    assert await irt_app.resolve_language_switch("   ", "en") is None
    assert await irt_app.resolve_language_switch("kannst du deutsch reden", "fr") is None
    assert check.calls == []


# --- the model's answer ----------------------------------------------------------


@pytest.mark.parametrize(
    ("current", "message", "answer", "expected"),
    [
        ("en", "kannst du deutsch reden", "DE", "de"),
        ("en", "kannst du deutsch reden", " de.\n", "de"),
        ("de", "I had a dream about my brother", "EN", "en"),
        ("en", "nicht im traum, generell", "OTHER", None),
        ("en", "nicht im traum, generell", "KEEP", None),  # not one of the words
        ("en", "nicht im traum, generell", "EN", None),  # the current language: keep
        ("de", "can we speak english please", "DE", None),
        ("en", "kannst du deutsch reden", "Deutsch, please", None),  # not one of the words
        ("en", "kannst du deutsch reden", "DE/EN", None),
        ("en", "kannst du deutsch reden", "EN or DE", None),
        ("en", "kannst du deutsch reden", "DE!", "de"),
        ("en", "kannst du deutsch reden", "", None),
    ],
)
async def test_the_answer_decides(
    check: _Check, current: str, message: str, answer: str, expected: str | None
) -> None:
    check.answer = answer
    assert await irt_app.resolve_language_switch(message, current) == expected
    assert len(check.calls) == 1


async def test_the_model_gets_only_the_message(check: _Check) -> None:
    check.answer = "DE"
    await irt_app.resolve_language_switch(" kannst du deutsch reden ", "en")
    assert check.calls == ["kannst du deutsch reden"]
    await irt_app.resolve_language_switch("can we speak english please", "de")
    assert check.calls[-1] == "can we speak english please"


async def test_an_error_keeps_the_language_and_is_not_remembered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(irt_app, "routing_agent", _Check(fail=RuntimeError("provider down")))
    monkeypatch.setattr(irt_app, "_language_verdicts", {})
    assert await irt_app.resolve_language_switch("kannst du deutsch reden", "en") is None
    assert irt_app._language_verdicts == {}


async def test_a_timeout_keeps_the_language(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(irt_app, "routing_agent", _Check(answer="DE", delay=1))
    monkeypatch.setattr(irt_app, "_language_verdicts", {})
    monkeypatch.setattr(irt_app, "_LANGUAGE_CHECK_TIMEOUT_S", 0.01)
    assert await irt_app.resolve_language_switch("kannst du deutsch reden", "en") is None
    assert irt_app._language_verdicts == {}


def test_the_model_gets_eight_seconds() -> None:
    assert irt_app._LANGUAGE_CHECK_TIMEOUT_S == 8.0


async def test_without_agents_the_language_stays(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(irt_app, "routing_agent", None)
    monkeypatch.setattr(irt_app, "_language_verdicts", {})
    assert await irt_app.resolve_language_switch("kannst du deutsch reden", "en") is None


async def test_an_answer_is_remembered_without_the_text(check: _Check) -> None:
    check.answer = "DE"
    assert await irt_app.resolve_language_switch("Kannst du Deutsch reden", "en") == "de"
    assert await irt_app.resolve_language_switch("kannst du deutsch reden ", "en") == "de"
    assert len(check.calls) == 1  # the second answer came from memory
    assert all("deutsch" not in key for key in irt_app._language_verdicts)
    # The same text in the other chat language is a different question.
    check.answer = "OTHER"
    assert await irt_app.resolve_language_switch("kannst du deutsch reden", "de") is None
    assert len(check.calls) == 1  # names no other language: the word check stops it


async def test_the_cache_is_keyed_by_language_and_text(check: _Check) -> None:
    check.answer = "EN"  # the chat's own language: kept
    assert await irt_app.resolve_language_switch("No German please", "en") is None
    check.answer = "EN"
    # Names a language in either chat ("German"), so it is asked again.
    assert await irt_app.resolve_language_switch("No German please", "de") == "en"
    assert len(check.calls) == 2
    assert len(irt_app._language_verdicts) == 2


# --- language_check, the entry point the backend calls ---------------------------


async def test_the_entry_point_reports_a_switch(check: _Check) -> None:
    check.answer = "DE"
    req = LanguageCheckRequest(message="kannst du deutsch reden", language=Language.EN)
    resp = await language_api.language_check(req)
    assert resp.model_dump(mode="json") == {"language": "de", "switched": True}


async def test_the_entry_point_reports_no_switch(check: _Check) -> None:
    req = LanguageCheckRequest(message="ok", language=Language.DE)
    resp = await language_api.language_check(req)
    assert resp.model_dump(mode="json") == {"language": "de", "switched": False}
    assert check.calls == []


@pytest.mark.parametrize(
    "body",
    [
        {"language": "en"},
        {"message": "hello there friend"},
        {"message": "bonjour", "language": "fr"},
        {"message": "hi", "language": "en", "session_id": "s"},  # extra fields are refused
    ],
)
def test_the_request_is_validated(body: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        LanguageCheckRequest.model_validate(body)


# --- the turn after a switch -----------------------------------------------------


def _conversation(language: str) -> Conversation:
    c = Conversation(session_id="s", language=language)
    c.add_message("A wolf stood at my door.", "user")
    c.stages = []
    return c


async def test_the_switch_note_is_added_only_on_the_switch() -> None:
    c = _conversation("de")
    assert await irt_app._stage_note("summary", None, c, "de") is None
    assert await irt_app._stage_note("summary", None, c, "de", False, True) == (
        LANGUAGE_SWITCH_NOTE_DE
    )
    assert await irt_app._stage_note("final", None, c, "en", False, True) == (
        LANGUAGE_SWITCH_NOTE_EN
    )
    # With another note, the switch note comes last.
    assert await irt_app._stage_note("rewriting", None, c, "de", True, True) == (
        REWRITING_NOTHING_YET_NOTE_DE + "\n\n" + LANGUAGE_SWITCH_NOTE_DE
    )


class _Turn:
    """Routing and response agent in one: routes to summary, records the reply prompt."""

    def __init__(self) -> None:
        self.system_prompts: list[str] = []

    async def generate(self, prompt: str, system_prompt: str | None = None, **kwargs: Any) -> Any:
        if system_prompt is None:
            return "summary", {}, None  # the router
        self.system_prompts.append(system_prompt)
        return "Antwort", {"input": 1, "output": 1, "total": 2}, None

    async def generate_stream(self, prompt: str, system_prompt: str | None = None) -> Any:
        self.system_prompts.append(system_prompt or "")
        yield "Antwort", None, None
        yield "", {"input": 1, "output": 1, "total": 2}, None


def _summary_ready() -> Conversation:
    c = Conversation(session_id="s", language="de")
    c.add_message("A wolf stood at my door.", "user")
    c.add_message("Shall we rewrite it?", "assistant", "recording")
    c.add_message("The wolf becomes my dog.", "user")
    c.add_message("Lovely. Shall I write the summary?", "assistant", "rewriting")
    c.stages = ["recording", "rewriting"]
    return c


@pytest.mark.parametrize("switched", [True, False])
@pytest.mark.parametrize("streaming", [True, False])
async def test_both_paths_add_the_note_only_on_the_switch(
    monkeypatch: pytest.MonkeyPatch, switched: bool, streaming: bool
) -> None:
    agent = _Turn()
    monkeypatch.setattr(irt_app, "routing_agent", agent)
    monkeypatch.setattr(irt_app, "response_agent", agent)

    async def tells_the_dream(text: str) -> bool:
        return False

    monkeypatch.setattr(irt_app, "_is_plain_talk", tells_the_dream)
    chat_input = ChatInput(
        session_id="s",
        message="Der Wolf wird mein Hund. Ja, bitte die Zusammenfassung.",
        language_override="de",
        language_switched=switched,
    )

    if streaming:
        frames = [
            f async for f in irt_app.process_chat_message_stream(chat_input, _summary_ready())
        ]
        assert frames[-1] == "data: [DONE]\n\n"
    else:
        await irt_app.process_chat_message(chat_input, _summary_ready())

    (system_prompt,) = agent.system_prompts
    # The scope boundary as composed for this turn (S08: helpline reference filled in).
    assert system_prompt.startswith(irt_app.scope_boundary(False))
    assert HELPLINE_REFERENCE not in system_prompt
    assert SYSTEM_PROMPT_TEMPLATES_DE["summary"] in system_prompt
    assert system_prompt.endswith(LANGUAGE_SWITCH_NOTE_DE) is switched
    assert LANGUAGE_SWITCH_NOTE_EN not in system_prompt
    assert SYSTEM_PROMPT_TEMPLATES_EN["summary"] not in system_prompt


async def test_the_chat_entry_point_passes_the_flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = []

    async def fake(
        chat_input: ChatInput, conversation: Conversation, safety_enabled: bool = True
    ) -> PortedResponse:
        seen.append(chat_input.language_switched)
        conversation.stages.append("recording")
        return PortedResponse(
            session_id="s", stage="recording", response="ok", stages=conversation.stages
        )

    monkeypatch.setattr(irt_app, "routing_agent", object())
    monkeypatch.setattr(irt_app, "response_agent", object())
    monkeypatch.setattr(irt_app, "process_chat_message", fake)
    for extra in ({"language_switched": True}, {}):
        req = ChatRequest.model_validate(
            {"messages": [{"role": "user", "content": "hi"}], "language": "en", **extra}
        )
        await chatmod.chat(req)
    assert seen == [True, False]


async def test_the_stream_entry_point_passes_the_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = []

    async def fake(
        chat_input: ChatInput, conversation: Conversation, safety_enabled: bool = True
    ) -> Any:
        seen.append(chat_input.language_switched)
        conversation.stages.append("recording")
        yield 'data: {"content": "ok"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(irt_app, "routing_agent", object())
    monkeypatch.setattr(irt_app, "response_agent", object())
    monkeypatch.setattr(irt_app, "process_chat_message_stream", fake)
    for extra in ({"language_switched": True}, {}):
        req = ChatRequest.model_validate(
            {"messages": [{"role": "user", "content": "hi"}], "language": "en", **extra}
        )
        frames = [f async for f in chatmod.chat_stream(req)]
        assert frames[-1] == "data: [DONE]\n\n"
    assert seen == [True, False]


# --- the prompts -----------------------------------------------------------------


def test_the_language_paragraphs_point_to_writing_in_the_other_language() -> None:
    assert (
        "\n\nLanguage: every reply is written in English. If the user asks to speak German, "
        "say briefly that they can simply write in German and the chat will continue in "
        "German. If they write in or ask for a language other than English or German, say in "
        "English that this chat can be held in English or German, and then carry on with "
        "what else they said.\n\n"
    ) in SCOPE_BOUNDARY_EN
    assert (
        "\n\nSprache: Jede Antwort ist auf Deutsch geschrieben. Bittet der Nutzer darum, "
        "Englisch zu sprechen, sag kurz, dass er einfach auf Englisch schreiben kann und der "
        "Chat dann auf Englisch weitergeht. Schreibt er in einer anderen Sprache als Deutsch "
        "oder Englisch oder bittet er um eine solche, sag auf Deutsch, dass dieser Chat auf "
        "Deutsch oder Englisch geführt werden kann, und geh dann auf das ein, was er sonst "
        "gesagt hat.\n\n"
    ) in SCOPE_BOUNDARY_DE
    for boundary in (SCOPE_BOUNDARY_EN, SCOPE_BOUNDARY_DE):
        assert "settings" not in boundary and "Einstellungen" not in boundary
        # The configurable helpline reference (S08) is untouched.
        assert boundary.count(HELPLINE_REFERENCE) == 1


def test_the_language_check_answers_in_one_word() -> None:
    assert LANGUAGE_CHECK_PROMPT.endswith("Answer only EN, DE or OTHER.")
    assert LANGUAGE_CHECK_PROMPT != TALK_GATE_PROMPT
