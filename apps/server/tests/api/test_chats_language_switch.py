"""Mid-chat language switch (en <-> de).

`_pre_turn` asks the AI's `language_check` about every user message, after the
daily-cap admission and persisting it, BEFORE the crisis classifier (and the
dev-flag skip). On a switch `chat.language` changes from that message on, so
the classifier, the helpline text and the AI turn all use the new language,
and the AI payload carries `language_switched=True` for that one turn. A
crisis turn that switched keeps the note pending for the next AI turn; a turn
whose user message is rolled back undoes its switch.
"""

from __future__ import annotations

import asyncio
import json
import logging
import types
from typing import Any

import pytest
from sqlalchemy import select

from redream_api.core import limits
from redream_api.core.config import settings
from redream_api.db.models.chat import Chat
from redream_api.db.models.chat_message import ChatMessage
from redream_api.db.models.dream import Dream
from redream_api.services import ai_client as ai_client_mod
from redream_api.services.ai_client import AIServiceError, InProcessAiClient

from .conftest import FakeAiClient, start_chat

_HIGH_RISK = {
    "risk": "high",
    "categories": ["suicidal_ideation"],
    "confidence": 0.9,
    "prompt_version": "safety-test",
}


def _reply(stage: str = "recording", **extra: Any) -> dict[str, Any]:
    return {
        "stage": stage,
        "response": "Erzähl mir mehr.",
        "structured": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
        "prompt_version": "test-v0",
        **extra,
    }


def _stream(reply: dict[str, Any] | None = None) -> list[str]:
    reply = reply or _reply()
    return [
        "data: " + json.dumps({"content": reply["response"]}),
        "data: " + json.dumps({"final": reply}),
    ]


def _methods(fake_ai: FakeAiClient) -> list[str]:
    return [c["method"] for c in fake_ai.calls]


def _payload(fake_ai: FakeAiClient, method: str) -> dict[str, Any]:
    return [c for c in fake_ai.calls if c["method"] == method][-1]["payload"]


async def _stored_chat(db_session_maker, chat_id: int) -> Chat:
    async with db_session_maker() as session:
        return (await session.scalars(select(Chat).where(Chat.id == chat_id))).one()


async def _stored_language(db_session_maker, chat_id: int) -> str:
    return (await _stored_chat(db_session_maker, chat_id)).language


async def _stored_messages(db_session_maker, chat_id: int) -> list[str]:
    async with db_session_maker() as session:
        rows = await session.scalars(
            select(ChatMessage).where(ChatMessage.chat_id == chat_id).order_by(ChatMessage.id)
        )
        return [m.content for m in rows]


# ---------------------------------------------------------------------------
# The switch and its place in the pre-turn order.
# ---------------------------------------------------------------------------


async def test_message_route_switches_before_the_classifier(
    client, fake_ai: FakeAiClient, db_session_maker
) -> None:
    chat_id = await start_chat(client, "en")
    fake_ai.queue_language("de")
    fake_ai.queue_chat(_reply())

    text = "Ich hatte einen Traum über meinen Bruder."
    r = await client.post(f"/api/v1/chats/{chat_id}/message", json={"content": text})

    assert r.status_code == 200, r.text
    assert _methods(fake_ai) == ["language_check", "safety_classify", "chat"]
    assert _payload(fake_ai, "language_check") == {"message": text, "language": "en"}
    assert _payload(fake_ai, "safety_classify")["language"] == "de"
    chat_payload = _payload(fake_ai, "chat")
    assert chat_payload["language"] == "de"
    assert chat_payload["language_switched"] is True
    assert {m["language"] for m in chat_payload["messages"]} == {"de"}
    assert await _stored_language(db_session_maker, chat_id) == "de"
    chats = (await client.get("/api/v1/chats")).json()
    assert chats[0]["language"] == "de"


async def test_stream_route_switches_before_the_classifier(
    client, fake_ai: FakeAiClient, db_session_maker
) -> None:
    chat_id = await start_chat(client, "de")
    fake_ai.queue_language("en")
    fake_ai.queue_chat_stream(_stream(_reply(response="Tell me more.")))

    r = await client.post(
        f"/api/v1/chats/{chat_id}/message/stream",
        json={"content": "I had a dream about my brother."},
    )

    assert r.status_code == 200, r.text
    assert "done" in r.text
    assert _methods(fake_ai) == ["language_check", "safety_classify", "chat_stream"]
    assert _payload(fake_ai, "safety_classify")["language"] == "en"
    stream_payload = _payload(fake_ai, "chat_stream")
    assert stream_payload["language"] == "en"
    assert stream_payload["language_switched"] is True
    assert await _stored_language(db_session_maker, chat_id) == "en"


async def test_the_check_runs_after_the_user_message_is_stored(
    client, fake_ai: FakeAiClient, db_session_maker, monkeypatch
) -> None:
    chat_id = await start_chat(client, "en")
    seen: list[list[str]] = []

    async def check(message: str, language: str) -> str:
        fake_ai.calls.append({"method": "language_check", "payload": {}})
        seen.append(await _stored_messages(db_session_maker, chat_id))
        return "de"

    monkeypatch.setattr(fake_ai, "language_check", check)
    fake_ai.queue_chat(_reply())

    r = await client.post(f"/api/v1/chats/{chat_id}/message", json={"content": "Ich bin da."})

    assert r.status_code == 200, r.text
    assert seen == [["Ich bin da."]]


async def test_the_switch_is_stored_before_the_classifier(
    client, fake_ai: FakeAiClient, db_session_maker, monkeypatch
) -> None:
    chat_id = await start_chat(client, "en")
    seen: list[str] = []
    classify = fake_ai.safety_classify

    async def check_stored(payload: dict[str, Any]) -> dict[str, Any]:
        seen.append(await _stored_language(db_session_maker, chat_id))
        return await classify(payload)

    monkeypatch.setattr(fake_ai, "safety_classify", check_stored)
    fake_ai.queue_language("de")
    fake_ai.queue_chat(_reply())

    r = await client.post(f"/api/v1/chats/{chat_id}/message", json={"content": "Ich bin da."})

    assert r.status_code == 200, r.text
    assert seen == ["de"]


async def test_over_the_daily_cap_nothing_is_checked(
    client, fake_ai: FakeAiClient, fake_user, day
) -> None:
    chat_id = await start_chat(client, "en")
    for _ in range(100):
        limits.count_ai_request(fake_user.id)
    fake_ai.queue_language("de")

    r = await client.post(f"/api/v1/chats/{chat_id}/message", json={"content": "Ich bin da."})

    assert r.status_code == 429
    assert fake_ai.calls == []


async def test_the_check_does_not_count_against_the_daily_cap(
    client, fake_ai: FakeAiClient, fake_user, day
) -> None:
    chat_id = await start_chat(client, "en")
    for _ in range(98):
        limits.count_ai_request(fake_user.id)
    fake_ai.queue_language("de")
    fake_ai.queue_chat(_reply())
    fake_ai.queue_chat(_reply())

    # The 99th and 100th turn, each with its language check: both admitted.
    for text in ("Ich bin müde.", "Ja."):
        r = await client.post(f"/api/v1/chats/{chat_id}/message", json={"content": text})
        assert r.status_code == 200, r.text
    assert _methods(fake_ai).count("language_check") == 2

    r = await client.post(f"/api/v1/chats/{chat_id}/message", json={"content": "Ja."})
    assert r.status_code == 429


async def test_switch_flag_is_only_set_on_the_switching_turn(
    client, fake_ai: FakeAiClient, db_session_maker
) -> None:
    chat_id = await start_chat(client, "en")
    fake_ai.queue_language("de")
    fake_ai.queue_chat(_reply())
    fake_ai.queue_chat(_reply())

    await client.post(f"/api/v1/chats/{chat_id}/message", json={"content": "Ich bin müde."})
    r = await client.post(f"/api/v1/chats/{chat_id}/message", json={"content": "Ja."})

    assert r.status_code == 200, r.text
    # Second turn: checked against the new language, no switch, no note.
    assert _payload(fake_ai, "language_check")["language"] == "de"
    chat_payload = _payload(fake_ai, "chat")
    assert chat_payload["language"] == "de"
    assert chat_payload["language_switched"] is False
    assert await _stored_language(db_session_maker, chat_id) == "de"


async def test_dev_flag_path_still_switches(
    client, fake_ai: FakeAiClient, db_session_maker, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "safety_classifier_enabled", False)
    chat_id = await start_chat(client, "en")
    fake_ai.queue_language("de")
    fake_ai.queue_chat(_reply())

    r = await client.post(
        f"/api/v1/chats/{chat_id}/message", json={"content": "Kannst du Deutsch reden?"}
    )

    assert r.status_code == 200, r.text
    assert _methods(fake_ai) == ["language_check", "chat"]
    chat_payload = _payload(fake_ai, "chat")
    assert chat_payload["language"] == "de"
    assert chat_payload["language_switched"] is True
    assert await _stored_language(db_session_maker, chat_id) == "de"


@pytest.mark.parametrize(("start", "new"), [("en", "de"), ("de", "en")])
async def test_crisis_message_in_the_new_language_gets_its_helpline(
    client, fake_ai: FakeAiClient, db_session_maker, monkeypatch, start, new
) -> None:
    monkeypatch.setattr(settings, "helpline_text_en", "Help EN.")
    monkeypatch.setattr(settings, "helpline_text_de", "Hilfe DE.")
    chat_id = await start_chat(client, start)
    fake_ai.queue_language(new)
    fake_ai.queue_safety(_HIGH_RISK)

    r = await client.post(
        f"/api/v1/chats/{chat_id}/message",
        json={"content": "Ich will nicht mehr hier sein."},
    )

    assert r.status_code == 200, r.text
    assert _payload(fake_ai, "safety_classify")["language"] == new
    assert r.json()["safety_event"]["message"] == settings.helpline_text_for(new)
    assert r.json()["safety_event"]["message"] == {"en": "Help EN.", "de": "Hilfe DE."}[new]
    assert "chat" not in _methods(fake_ai)
    stored = await _stored_chat(db_session_maker, chat_id)
    assert stored.language == new
    assert stored.language_switch_pending is True


async def test_no_switch_keeps_language_and_flag_false(
    client, fake_ai: FakeAiClient, db_session_maker
) -> None:
    chat_id = await start_chat(client, "en")
    fake_ai.queue_chat(_reply())  # language_check default: the current language

    r = await client.post(f"/api/v1/chats/{chat_id}/message", json={"content": "I was in Berlin."})

    assert r.status_code == 200, r.text
    assert _payload(fake_ai, "safety_classify")["language"] == "en"
    chat_payload = _payload(fake_ai, "chat")
    assert chat_payload["language"] == "en"
    assert chat_payload["language_switched"] is False
    assert await _stored_language(db_session_maker, chat_id) == "en"


async def test_unsupported_check_answer_keeps_language(
    client, fake_ai: FakeAiClient, db_session_maker
) -> None:
    chat_id = await start_chat(client, "en")
    fake_ai.queue_language("fr")
    fake_ai.queue_chat(_reply())

    r = await client.post(f"/api/v1/chats/{chat_id}/message", json={"content": "Bonjour"})

    assert r.status_code == 200, r.text
    assert _payload(fake_ai, "chat")["language_switched"] is False
    assert await _stored_language(db_session_maker, chat_id) == "en"


async def test_whitespace_only_message_skips_the_check(client, fake_ai: FakeAiClient) -> None:
    chat_id = await start_chat(client, "en")
    fake_ai.queue_chat(_reply())

    r = await client.post(f"/api/v1/chats/{chat_id}/message", json={"content": "   "})

    assert r.status_code == 200, r.text
    assert "language_check" not in _methods(fake_ai)


# ---------------------------------------------------------------------------
# A rolled-back user message undoes its switch.
# ---------------------------------------------------------------------------


async def test_failed_ai_turn_reverts_the_switch(
    client, fake_ai: FakeAiClient, db_session_maker
) -> None:
    """The 502 path drops the user message so the client can resend it; the
    switch it caused is undone too, so the retry switches (with the note)."""
    chat_id = await start_chat(client, "en")
    fake_ai.queue_language("de")
    fake_ai.queue_chat_error(AIServiceError("boom"))

    r = await client.post(f"/api/v1/chats/{chat_id}/message", json={"content": "Ich bin da."})

    assert r.status_code == 502
    assert await _stored_language(db_session_maker, chat_id) == "en"
    assert await _stored_messages(db_session_maker, chat_id) == []


async def test_failed_stream_reverts_the_switch(
    client, fake_ai: FakeAiClient, db_session_maker
) -> None:
    chat_id = await start_chat(client, "en")
    fake_ai.queue_language("de")
    fake_ai.queue_chat_stream(["data: " + json.dumps({"error": "upstream exploded"})])

    r = await client.post(
        f"/api/v1/chats/{chat_id}/message/stream", json={"content": "Ich bin da."}
    )

    assert '"error"' in r.text
    assert await _stored_language(db_session_maker, chat_id) == "en"
    assert await _stored_messages(db_session_maker, chat_id) == []


async def test_a_stream_that_raises_reverts_the_switch(
    client, fake_ai: FakeAiClient, db_session_maker
) -> None:
    chat_id = await start_chat(client, "en")
    fake_ai.queue_language("de")
    fake_ai.queue_chat_stream_error(AIServiceError("chat_stream timed out after 90s"))

    r = await client.post(
        f"/api/v1/chats/{chat_id}/message/stream", json={"content": "Ich bin da."}
    )

    assert '"error"' in r.text
    assert await _stored_language(db_session_maker, chat_id) == "en"


async def test_a_disconnect_before_finalize_reverts_the_switch(
    db, fake_user, fake_ai: FakeAiClient, db_session_maker
) -> None:
    from redream_api.api.v1.chats import routes as chats_routes
    from redream_api.api.v1.chats.schemas import ChatMessageRequest
    from redream_api.db.repositories.chat_repo import ChatRepo

    chat = await ChatRepo(db).create(user_id=fake_user.id, language="en")
    chat_id = chat.id
    fake_ai.queue_language("de")
    fake_ai.queue_chat_stream(["data: " + json.dumps({"content": "Erzähl "})])
    setup = await chats_routes._pre_turn(
        chat_id, ChatMessageRequest(content="Ich bin da."), fake_user, db, fake_ai
    )

    gen = chats_routes._stream_ai_turn(db, fake_ai, fake_user, setup)
    assert "user_message" in await gen.__anext__()
    assert "content" in await gen.__anext__()
    await gen.aclose()

    assert await _stored_language(db_session_maker, chat_id) == "en"
    assert await _stored_messages(db_session_maker, chat_id) == []


@pytest.mark.parametrize("route", ["message", "message/stream"])
async def test_failed_crisis_check_reverts_the_switch(
    client, fake_ai: FakeAiClient, db_session_maker, route
) -> None:
    """A 502 from the crisis check drops the user message (pre-stream on the
    stream route) and undoes the switch like a failed AI turn, so the client's
    retry switches again and carries the note."""
    chat_id = await start_chat(client, "en")
    fake_ai.queue_language("de")
    fake_ai.queue_safety_error(AIServiceError("classifier down"))
    url = f"/api/v1/chats/{chat_id}/{route}"

    r = await client.post(url, json={"content": "Ich bin da."})

    assert r.status_code == 502
    assert r.json()["detail"].startswith("safety: ")
    assert await _stored_language(db_session_maker, chat_id) == "en"
    assert await _stored_messages(db_session_maker, chat_id) == []

    fake_ai.queue_language("de")
    if route == "message":
        fake_ai.queue_chat(_reply())
    else:
        fake_ai.queue_chat_stream(_stream())
    r = await client.post(url, json={"content": "Ich bin da."})

    assert r.status_code == 200, r.text
    assert _payload(fake_ai, "language_check")["language"] == "en"
    method = "chat" if route == "message" else "chat_stream"
    assert _payload(fake_ai, method)["language_switched"] is True
    assert await _stored_language(db_session_maker, chat_id) == "de"


# ---------------------------------------------------------------------------
# A crisis turn that switched: the note goes with the next AI turn.
# ---------------------------------------------------------------------------


async def _crisis_switch(client, fake_ai: FakeAiClient) -> int:
    """An English chat whose first message is a German crisis message."""
    chat_id = await start_chat(client, "en")
    fake_ai.queue_language("de")
    fake_ai.queue_safety({"risk": "high", "categories": ["self_harm"], "confidence": 0.9})
    r = await client.post(
        f"/api/v1/chats/{chat_id}/message", json={"content": "Ich will nicht mehr leben."}
    )
    assert r.status_code == 200, r.text
    assert r.json()["safety_event"]["message"] == settings.helpline_text_for("de")
    return chat_id


async def test_switch_on_a_crisis_turn_notes_it_on_the_next_ai_turn(
    client, fake_ai: FakeAiClient, db_session_maker
) -> None:
    """The crisis turn has no AI reply, so the next AI turn carries the note,
    once."""
    chat_id = await _crisis_switch(client, fake_ai)
    fake_ai.queue_chat(_reply())
    fake_ai.queue_chat(_reply())

    wolf = {"content": "Der Wolf war groß."}
    r = await client.post(f"/api/v1/chats/{chat_id}/message", json=wolf)
    assert r.status_code == 200, r.text
    assert _payload(fake_ai, "language_check")["language"] == "de"
    chat_payload = _payload(fake_ai, "chat")
    assert chat_payload["language"] == "de"
    assert chat_payload["language_switched"] is True
    assert (await _stored_chat(db_session_maker, chat_id)).language_switch_pending is False

    r = await client.post(f"/api/v1/chats/{chat_id}/message", json={"content": "Ja."})
    assert r.status_code == 200, r.text
    assert _payload(fake_ai, "chat")["language_switched"] is False
    assert await _stored_language(db_session_maker, chat_id) == "de"


@pytest.mark.parametrize("route", ["message", "message/stream"])
async def test_a_crisis_turn_without_a_switch_leaves_no_note(
    client, fake_ai: FakeAiClient, db_session_maker, route
) -> None:
    chat_id = await start_chat(client, "en")
    fake_ai.queue_safety(_HIGH_RISK)  # language_check default: the current language
    url = f"/api/v1/chats/{chat_id}/{route}"

    r = await client.post(url, json={"content": "I don't want to live anymore."})

    assert r.status_code == 200, r.text
    assert "chat" not in _methods(fake_ai) and "chat_stream" not in _methods(fake_ai)
    stored = await _stored_chat(db_session_maker, chat_id)
    assert (stored.language, stored.language_switch_pending) == ("en", False)

    if route == "message":
        fake_ai.queue_chat(_reply())
    else:
        fake_ai.queue_chat_stream(_stream())
    r = await client.post(url, json={"content": "The wolf was big."})

    assert r.status_code == 200, r.text
    method = "chat" if route == "message" else "chat_stream"
    assert _payload(fake_ai, method)["language_switched"] is False


@pytest.mark.parametrize("route", ["message", "message/stream"])
async def test_a_crisis_turn_that_switches_logs_no_chat_id(
    client, fake_ai: FakeAiClient, caplog: pytest.LogCaptureFixture, route
) -> None:
    """S05: the crisis warning carries no id, and neither may the switch line
    logged just before it by the same request."""
    chat_id = await start_chat(client, "en")
    fake_ai.queue_language("de")
    fake_ai.queue_safety(_HIGH_RISK)
    caplog.clear()

    with caplog.at_level(logging.INFO, logger="redream_api"):
        r = await client.post(
            f"/api/v1/chats/{chat_id}/{route}", json={"content": "Ich will sterben."}
        )

    assert r.status_code == 200, r.text
    records = [rec for rec in caplog.records if rec.name.startswith("redream_api")]
    messages = [rec.getMessage() for rec in records]
    assert "crisis check: high risk" in messages
    assert any("switched en -> de" in m for m in messages)
    assert all(chat_id not in (rec.args or ()) for rec in records)
    assert all(str(chat_id) not in m for m in messages)


async def test_pending_switch_note_survives_a_failed_turn_and_streams(
    client, fake_ai: FakeAiClient, db_session_maker
) -> None:
    chat_id = await _crisis_switch(client, fake_ai)
    fake_ai.queue_chat_error(AIServiceError("boom"))
    wolf = {"content": "Der Wolf war groß."}
    r = await client.post(f"/api/v1/chats/{chat_id}/message", json=wolf)
    assert r.status_code == 502
    stored = await _stored_chat(db_session_maker, chat_id)
    # The failed turn did not switch, so nothing is undone and the note waits.
    assert (stored.language, stored.language_switch_pending) == ("de", True)

    fake_ai.queue_chat_stream(_stream())
    r = await client.post(f"/api/v1/chats/{chat_id}/message/stream", json=wolf)
    assert "done" in r.text
    assert _payload(fake_ai, "chat_stream")["language_switched"] is True

    fake_ai.queue_chat(_reply())
    r = await client.post(f"/api/v1/chats/{chat_id}/message", json={"content": "Ja."})
    assert r.status_code == 200, r.text
    assert _payload(fake_ai, "chat")["language_switched"] is False


# ---------------------------------------------------------------------------
# The chat's dream keeps the chat's language at the summary.
# ---------------------------------------------------------------------------


async def test_summary_dream_stores_the_chat_language(
    client, fake_ai: FakeAiClient, db_session_maker
) -> None:
    chat_id = await start_chat(client, "en")
    structured = {
        "title": "Der helle Wald",
        "original_summary": "Verfolgt im Wald.",
        "rewritten_summary": "Ich gehe durch einen hellen Wald.",
    }
    fake_ai.queue_language("de")
    fake_ai.queue_chat(_reply("summary", structured=structured))

    r = await client.post(
        f"/api/v1/chats/{chat_id}/message", json={"content": "Ja, das passt so für mich."}
    )
    assert r.status_code == 200, r.text
    (dream,) = (await client.get("/api/v1/dreams")).json()
    assert dream["language"] == "de"

    # A later summary in the other language updates the same dream.
    fake_ai.queue_language("en")
    fake_ai.queue_chat(_reply("summary", structured=structured))
    r = await client.post(
        f"/api/v1/chats/{chat_id}/message", json={"content": "Can we finish this in English?"}
    )
    assert r.status_code == 200, r.text
    (dream,) = (await client.get("/api/v1/dreams")).json()
    assert dream["language"] == "en"
    async with db_session_maker() as session:
        (row,) = (await session.scalars(select(Dream))).all()
    assert row.language == "en"


async def test_streamed_summary_dream_stores_the_chat_language(
    client, fake_ai: FakeAiClient
) -> None:
    chat_id = await start_chat(client, "de")
    structured = {
        "title": "The bright forest",
        "original_summary": "Chased in a forest.",
        "rewritten_summary": "I walk through a bright forest.",
    }
    fake_ai.queue_language("en")
    fake_ai.queue_chat_stream(_stream(_reply("summary", structured=structured)))

    r = await client.post(
        f"/api/v1/chats/{chat_id}/message/stream", json={"content": "Yes, that works for me."}
    )
    assert "done" in r.text
    (dream,) = (await client.get("/api/v1/dreams")).json()
    assert dream["language"] == "en"


# ---------------------------------------------------------------------------
# InProcessAiClient.language_check — never raises, any failure keeps the language.
# ---------------------------------------------------------------------------


@pytest.fixture
def language_api(monkeypatch) -> types.ModuleType:
    """redream_ai.api.language, with the core's agents stubbed (no LLM)."""
    import redream_ai.api.language as language
    from redream_ai.core import irt_app

    monkeypatch.setattr(irt_app, "routing_agent", object())
    monkeypatch.setattr(irt_app, "response_agent", object())
    return language


async def test_in_process_check_returns_the_new_language(language_api, monkeypatch) -> None:
    from redream_ai.core import irt_app

    seen: list[tuple[str, str]] = []

    async def resolve(message: str, current: str) -> str | None:
        seen.append((message, current))
        return "de"

    monkeypatch.setattr(irt_app, "resolve_language_switch", resolve)

    assert await InProcessAiClient().language_check("Ich bin da.", "en") == "de"
    assert seen == [("Ich bin da.", "en")]


async def test_in_process_check_without_a_switch_keeps_the_language(
    language_api, monkeypatch
) -> None:
    from redream_ai.core import irt_app

    async def resolve(message: str, current: str) -> None:
        return None

    monkeypatch.setattr(irt_app, "resolve_language_switch", resolve)

    assert await InProcessAiClient().language_check("ok", "de") == "de"


def test_in_process_check_has_a_short_budget() -> None:
    assert ai_client_mod._LANGUAGE_CHECK_TIMEOUT == 10.0


async def test_in_process_check_failures_keep_the_language(
    language_api, monkeypatch, caplog
) -> None:
    from redream_ai.models import Language, LanguageCheckResponse

    async def boom(req: Any) -> Any:
        raise RuntimeError("unexpected")

    async def slow(req: Any) -> Any:
        # A valid switch, too late: only the deadline keeps the language.
        await asyncio.sleep(0.5)
        return LanguageCheckResponse(language=Language.DE, switched=True)

    async def unsupported(req: Any) -> Any:
        return types.SimpleNamespace(language=types.SimpleNamespace(value="fr"))

    secret = "mein geheimer Traum"
    monkeypatch.setattr(ai_client_mod, "_LANGUAGE_CHECK_TIMEOUT", 0.01)
    client = InProcessAiClient()
    with caplog.at_level(logging.WARNING):
        for fn in (boom, slow, unsupported):
            monkeypatch.setattr(language_api, "language_check", fn)
            assert await client.language_check(secret, "en") == "en"
        # A chat language the AI side does not take is refused there: kept too.
        assert await client.language_check(secret, "fr") == "fr"

    warnings = [r for r in caplog.records if "language_check" in r.getMessage()]
    assert len(warnings) == 4
    assert all(r.levelno == logging.WARNING for r in warnings)
    assert secret not in caplog.text


async def test_in_process_check_lets_cancellation_through(language_api, monkeypatch) -> None:
    async def cancelled(req: Any) -> Any:
        raise asyncio.CancelledError

    monkeypatch.setattr(language_api, "language_check", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await InProcessAiClient().language_check("Ich bin da.", "en")


def test_the_payload_carries_the_flag() -> None:
    from redream_api.api.v1.chats.routes import _ai_chat_payload

    chat = Chat(id=7, language="de")
    assert _ai_chat_payload(chat, [])["language_switched"] is False
    assert _ai_chat_payload(chat, [], language_switched=True)["language_switched"] is True


def test_the_pending_flag_is_a_not_null_column_defaulting_to_false() -> None:
    column = Chat.__table__.columns["language_switch_pending"]
    assert column.nullable is False
    assert column.default.arg is False
    assert column.server_default is not None
