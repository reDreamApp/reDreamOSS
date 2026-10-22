"""The checks around the rewriting stage (irt_app), without network.

Background (limit tests of 2026-10-14): the rewrite-safety critic flagged
plain replies such as "yes" or "that's it" as a replay of the dream, a user
who had nothing more to add was asked again and again, and a user who only
answered "yes" ended up with a summary of a new version nobody had described.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from redream_ai.core import irt_app
from redream_ai.core.models import Conversation
from redream_ai.core.prompts import (
    RECORDING_ENOUGH_NOTE_DE,
    RECORDING_ENOUGH_NOTE_EN,
    RECORDING_NO_DREAM_YET_NOTE_DE,
    RECORDING_NO_DREAM_YET_NOTE_EN,
    REPLAY_CHECK_PROMPT,
    REWRITING_ENOUGH_NOTE_DE,
    REWRITING_ENOUGH_NOTE_EN,
    REWRITING_NOTHING_YET_NOTE_EN,
    REWRITING_ONE_MORE_NOTE_DE,
    REWRITING_ONE_MORE_NOTE_EN,
    REWRITING_OPENER_NOTE_DE,
    REWRITING_OPENER_NOTE_EN,
    SAFETY_NOTE_INTRO_EN,
    SAFETY_NOTE_STYLE_DE,
    SAFETY_NOTE_STYLE_EN,
    TALK_GATE_PROMPT,
)

_TALK = {
    "yes",
    "that's it.",
    "no, nothing else.",
    "done.",
    "that is the whole new version",
    "idk",
    "are you a therapist?",
    "i want to stop.",
}


class _Checks:
    """Stands in for the routing agent: answers both one-word checks."""

    def __init__(self, changed: str = "CHANGED", fail: bool = False):
        self.changed = changed
        self.fail = fail
        self.calls: list[str] = []

    async def generate(self, prompt, system_prompt=None, **kwargs):
        if self.fail:
            raise RuntimeError("provider down")
        if system_prompt == TALK_GATE_PROMPT:
            text = prompt.removeprefix("Message: ")
            self.calls.append(f"talk:{text}")
            return ("TALK" if text.lower() in _TALK else "DREAM"), {}, None
        assert system_prompt == REPLAY_CHECK_PROMPT
        self.calls.append("replay")
        return self.changed, {}, None


@pytest.fixture
def checks(monkeypatch):
    agent = _Checks()
    monkeypatch.setattr(irt_app, "routing_agent", agent)
    monkeypatch.setattr(irt_app, "_talk_verdicts", {})
    return agent


def _segment(text: str, node_id: str | None, node_type: str | None):
    return SimpleNamespace(text=text, node_id=node_id, node_type=node_type)


def _flag(node_id: str, *segments):
    return SimpleNamespace(
        is_safe=False, node_id=node_id, segments=list(segments), constraint="NOTE"
    )


def _conversation(*turns: tuple[str, str, str | None], language: str = "en") -> Conversation:
    c = Conversation(
        session_id="s", language=language, nightmare_summary="A wolf stands at the door."
    )
    for role, content, stage in turns:
        c.add_message(content, role, stage)
    c.stages = [stage for role, _, stage in turns if role == "assistant"]
    return c


_OPENER = ("assistant", "Which part would you like to change?", "rewriting")


# --- plain replies -------------------------------------------------------------


async def test_a_plain_reply_is_recognised_and_remembered(checks):
    assert await irt_app._is_plain_talk("Yes") is True
    assert await irt_app._is_plain_talk(" yes ") is True
    assert checks.calls == ["talk:Yes"]  # the second answer came from memory
    assert all("yes" not in key for key in irt_app._talk_verdicts)  # no user text is kept


async def test_dream_content_is_not_plain_talk(checks):
    assert await irt_app._is_plain_talk("I stab him.") is False


async def test_a_long_message_goes_to_the_critic_unasked(checks):
    assert await irt_app._is_plain_talk("yes " * 11) is False
    assert checks.calls == []


async def test_symbols_alone_are_plain_talk(checks):
    assert await irt_app._is_plain_talk("😱😱 ...") is True
    assert checks.calls == []


async def test_a_failing_check_sends_the_message_to_the_critic(monkeypatch):
    monkeypatch.setattr(irt_app, "routing_agent", _Checks(fail=True))
    monkeypatch.setattr(irt_app, "_talk_verdicts", {})
    assert await irt_app._is_plain_talk("yes") is False
    assert irt_app._talk_verdicts == {}


async def test_the_critic_does_not_run_on_a_plain_reply(checks, monkeypatch):
    class _Critic:
        async def evaluate_intervention(self, *args, **kwargs):
            raise AssertionError("the critic must not see a plain reply")

    monkeypatch.setattr(irt_app, "safety_critic", _Critic())
    assert await irt_app.evaluate_safety("yes", _conversation()) is None


# --- false alarms ---------------------------------------------------------------


async def test_a_flagged_closing_remark_is_a_false_alarm(checks):
    result = _flag(
        "TRAUMA_REPLAY",
        _segment("We look at photo albums", "AFFECT_EXPRESSION", "neutral"),
        _segment("That is the whole new version", "TRAUMA_REPLAY", "maladaptive"),
    )
    assert await irt_app._is_false_alarm(result, "…", "summary") is True
    assert "replay" not in checks.calls


async def test_a_replay_verdict_on_a_changed_dream_is_a_false_alarm(checks):
    result = _flag("TRAUMA_REPLAY", _segment("Mr Brandt shrinks", "TRAUMA_REPLAY", "maladaptive"))
    assert (
        await irt_app._is_false_alarm(result, "Mr Brandt shrinks and apologises", "summary") is True
    )


async def test_a_real_replay_keeps_its_flag(checks):
    checks.changed = "SAME"
    result = _flag("TRAUMA_REPLAY", _segment("it happens again", "TRAUMA_REPLAY", "maladaptive"))
    assert await irt_app._is_false_alarm(result, "it happens again", "summary") is False


async def test_a_message_flagged_as_a_whole_is_checked_too(checks):
    whole = _flag("TRAUMA_REPLAY", _segment("…", None, None))
    assert await irt_app._is_false_alarm(whole, "…", "summary") is True
    checks.changed = "SAME"
    assert await irt_app._is_false_alarm(whole, "…", "summary") is False


@pytest.mark.parametrize("node", ["VIOLENT_REVENGE", "INTERRUPTION", "SUPPRESSION", "AVOIDANCE"])
async def test_every_other_verdict_stands(checks, node):
    result = _flag(node, _segment("I stab him", node, "maladaptive"))
    assert await irt_app._is_false_alarm(result, "I stab him", "summary") is False
    assert "replay" not in checks.calls
    # ... also next to a mistaken replay: the more serious part still counts.
    mixed = _flag(
        "TRAUMA_REPLAY",
        _segment("he shrinks", "TRAUMA_REPLAY", "maladaptive"),
        _segment("I stab him", node, "maladaptive"),
    )
    assert await irt_app._is_false_alarm(mixed, "he shrinks and I stab him", "summary") is False


async def test_without_the_original_dream_a_replay_verdict_stands(checks):
    result = _flag("TRAUMA_REPLAY", _segment("he shrinks", "TRAUMA_REPLAY", "maladaptive"))
    assert await irt_app._is_false_alarm(result, "he shrinks", None) is False


async def test_a_failing_replay_check_keeps_the_flag(monkeypatch):
    monkeypatch.setattr(irt_app, "routing_agent", _Checks(fail=True))
    assert await irt_app._describes_a_change("he shrinks", "summary") is False


# --- the critic's note ----------------------------------------------------------


def test_a_flag_reaches_the_reply_framed_not_to_be_quoted():
    note = irt_app._safety_note(_flag("VIOLENT_REVENGE"), True)
    assert note.startswith(SAFETY_NOTE_INTRO_EN)
    assert "\n\nNOTE\n\n" in note
    assert note.endswith(SAFETY_NOTE_STYLE_EN)
    assert irt_app._safety_note(_flag("VIOLENT_REVENGE"), False).endswith(SAFETY_NOTE_STYLE_DE)


def test_a_note_without_a_flag_is_passed_on_as_it_is():
    safe = SimpleNamespace(is_safe=True, constraint="conversational")
    assert irt_app._safety_note(safe, True) == "conversational"
    assert irt_app._safety_note(None, True) is None
    assert irt_app._safety_note(SimpleNamespace(is_safe=True, constraint=None), True) is None


# --- a user with nothing more to add ---------------------------------------------


def _note(c: Conversation, stage: str = "rewriting", language: str = "en", flag=None):
    return irt_app._stage_note(stage, flag, c, language)


async def test_the_first_closing_remark_gets_one_more_question(checks):
    c = _conversation(
        _OPENER, ("user", "I turn on a flashlight.", None), ("user", "That's it.", None)
    )
    assert await _note(c) == REWRITING_ONE_MORE_NOTE_EN
    assert await _note(c, language="de") == REWRITING_ONE_MORE_NOTE_DE


async def test_the_second_closing_remark_leads_to_the_summary_offer(checks):
    c = _conversation(
        _OPENER,
        ("user", "I turn on a flashlight.", None),
        ("assistant", "What happens next?", "rewriting"),
        ("user", "That's it.", None),
        ("assistant", "How does it end?", "rewriting"),
        ("user", "No, nothing else.", None),
    )
    assert await _note(c) == REWRITING_ENOUGH_NOTE_EN
    assert await _note(c, language="de") == REWRITING_ENOUGH_NOTE_DE


async def test_closing_remarks_count_even_with_content_between_them(checks):
    c = _conversation(
        _OPENER,
        ("user", "I fly.", None),
        ("user", "idk", None),
        ("user", "I land softly in the park.", None),
        ("user", "yes", None),
    )
    assert await _note(c) == REWRITING_ENOUGH_NOTE_EN


async def test_new_content_gets_no_note(checks):
    c = _conversation(
        _OPENER, ("user", "idk", None), ("user", "yes", None), ("user", "I fly away.", None)
    )
    assert await _note(c) is None


async def test_empty_answers_without_any_change_get_the_other_note(checks):
    # Also the first one: "yes please summarise" must not get a summary offer.
    c = _conversation(_OPENER, ("user", "idk", None))
    assert await _note(c) == REWRITING_NOTHING_YET_NOTE_EN
    c = _conversation(_OPENER, ("user", "idk", None), ("user", "yes", None))
    assert await _note(c) == REWRITING_NOTHING_YET_NOTE_EN


async def test_the_agreement_that_opened_the_stage_does_not_count(checks):
    c = _conversation(("user", "yes", None), _OPENER, ("user", "idk", None))
    assert await _note(c) == REWRITING_NOTHING_YET_NOTE_EN


async def test_the_stage_opens_with_its_question_whatever_the_agreement_says(checks):
    told = ("user", "A wolf stood in my bedroom door and I could not move.", None)
    offer = ("assistant", "Would you like to start rewriting?", "recording")
    c = _conversation(told, offer, ("user", "yes", None))
    assert await _note(c) == REWRITING_OPENER_NOTE_EN
    assert await _note(c, language="de") == REWRITING_OPENER_NOTE_DE
    # A first message that already changes something is left to the stage prompt.
    c = _conversation(told, offer, ("user", "Yes, the wolf turns into my dog.", None))
    assert await _note(c) is None


async def test_a_flag_takes_precedence_over_the_reminder(checks):
    c = _conversation(_OPENER, ("user", "idk", None), ("user", "yes", None))
    note = await _note(c, flag=_flag("VIOLENT_REVENGE"))
    assert note.startswith(SAFETY_NOTE_INTRO_EN)


async def test_the_telling_of_the_dream_gets_the_same_reminder(checks):
    told = ("user", "A wolf stood in my bedroom door and I could not move.", None)
    # "That's it." and a "yes" to "anything else?" still get their follow-up.
    c = _conversation(told, ("user", "That's it.", None), ("user", "yes", None))
    assert await _note(c, "recording") is None

    c = _conversation(
        told, ("user", "That's it.", None), ("user", "yes", None), ("user", "yes", None)
    )
    assert await _note(c, "recording") == RECORDING_ENOUGH_NOTE_EN
    assert await _note(c, "recording", "de") == RECORDING_ENOUGH_NOTE_DE


async def test_questions_before_the_dream_do_not_count(checks):
    c = _conversation(
        ("user", "Are you a therapist?", None),
        ("user", "idk", None),
        ("user", "A wolf stood in my bedroom door and I could not move.", None),
        ("user", "That's it.", None),
    )
    assert await _note(c, "recording") is None


async def test_a_second_message_without_a_dream_is_not_invited_again(checks):
    c = _conversation(("user", "Are you a therapist?", None))
    assert await _note(c, "recording") is None

    c = _conversation(("user", "Are you a therapist?", None), ("user", "idk", None))
    assert await _note(c, "recording") == RECORDING_NO_DREAM_YET_NOTE_EN
    assert await _note(c, "recording", "de") == RECORDING_NO_DREAM_YET_NOTE_DE


async def test_the_later_stages_get_no_note(checks):
    c = _conversation(_OPENER, ("user", "idk", None), ("user", "yes", None))
    assert await _note(c, "summary") is None
    assert await _note(c, "final") is None


# --- no summary without a new version ---------------------------------------------


async def test_the_summary_waits_until_something_was_changed(checks):
    c = _conversation(_OPENER, ("user", "yes", None), ("user", "yes", None))
    c.stages = ["rewriting", "summary"]  # this turn was routed to the summary

    assert await irt_app._hold_summary_without_rewrite("summary", c) == ("rewriting", True)
    assert c.stages == ["rewriting", "rewriting"]
    assert (
        await irt_app._stage_note("rewriting", None, c, "en", True) == REWRITING_NOTHING_YET_NOTE_EN
    )


async def test_the_summary_goes_ahead_with_a_new_version(checks):
    c = _conversation(_OPENER, ("user", "I turn on a flashlight.", None), ("user", "yes", None))
    c.stages = ["rewriting", "summary"]

    assert await irt_app._hold_summary_without_rewrite("summary", c) == ("summary", False)
    assert c.stages == ["rewriting", "summary"]


async def test_a_summary_that_was_already_shown_can_be_repeated(checks):
    c = _conversation(_OPENER, ("user", "yes", None))
    c.stages = ["rewriting", "summary", "summary"]

    assert await irt_app._hold_summary_without_rewrite("summary", c) == ("summary", False)


async def test_other_stages_are_left_alone(checks):
    c = _conversation(_OPENER, ("user", "yes", None))
    c.stages = ["rewriting", "rewriting"]

    assert await irt_app._hold_summary_without_rewrite("rewriting", c) == ("rewriting", False)
    assert checks.calls == []
