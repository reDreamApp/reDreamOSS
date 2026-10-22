"""Regression tests for the 2026-06-12 chat-flow fixes.

Covers: chat title from first message, intensity rating persisted onto the
Dream, safety classifier receiving stage+context, helpline text in the safety
payload, orphaned-user-message cleanup on AI failure, and safety-flagged
messages being excluded from forwarded history.
"""

from __future__ import annotations

from sqlalchemy import select

from redream_api.db.models.chat import Chat
from redream_api.db.models.dream import Dream
from redream_api.services.ai_client import AIServiceError

from .conftest import FakeAiClient, start_chat


def _chat_response(stage: str, response: str, structured: dict | None = None) -> dict:
    return {
        "stage": stage,
        "response": response,
        "structured": structured,
        "usage": {"input_tokens": 10, "output_tokens": 10},
        "prompt_version": "prompts-en-v1.0.0",
    }


async def test_full_flow_persists_title_and_intensity(
    client, fake_ai: FakeAiClient, fake_user, db_session_maker
) -> None:
    chat_id = await start_chat(client)

    fake_ai.queue_chat(_chat_response("intensity", "On a scale from 0 to 10?"))
    r = await client.post(
        f"/api/v1/chats/{chat_id}/message",
        json={"content": "I dreamt I was chased through a forest. That's all of it."},
    )
    assert r.status_code == 200, r.text

    fake_ai.queue_chat(_chat_response("rewriting", "Thanks. Let's rewrite it."))
    r = await client.post(
        f"/api/v1/chats/{chat_id}/message",
        json={"content": "It is an 8 out of 10."},
    )
    assert r.status_code == 200, r.text

    fake_ai.queue_chat(
        _chat_response(
            "summary",
            "Title: ...",
            structured={
                "title": "Bright forest",
                "original_summary": "Chased through a dark forest.",
                "rewritten_summary": "A calm walk in a sunlit forest.",
                "dream_summary": "Chased through a dark forest.",
            },
        )
    )
    r = await client.post(
        f"/api/v1/chats/{chat_id}/message",
        json={"content": "Yes, I'm satisfied. Please summarize."},
    )
    assert r.status_code == 200, r.text

    async with db_session_maker() as session:
        chat = await session.get(Chat, chat_id)
        dreams = (
            await session.scalars(select(Dream).where(Dream.user_id == fake_user.id))
        ).all()

    assert chat.title == "I dreamt I was chased through a forest. That's all of it."
    assert chat.is_nightmare is True
    assert len(dreams) == 1
    assert dreams[0].intensity == 8, "0-10 rating from the intensity stage must persist"


async def test_structured_intensity_wins_over_history_regex(
    client, fake_ai: FakeAiClient, fake_user, db_session_maker
) -> None:
    """The AI's structured intensity field is authoritative over the regex fallback."""
    chat_id = await start_chat(client)

    fake_ai.queue_chat(
        _chat_response(
            "summary",
            "Title: ...",
            structured={
                "title": "T",
                "original_summary": "O",
                "rewritten_summary": "R",
                "dream_summary": "O",
                "intensity": 7,
            },
        )
    )
    r = await client.post(
        f"/api/v1/chats/{chat_id}/message",
        json={"content": "It was an 8 out of 10, but anyway, summarize."},
    )
    assert r.status_code == 200, r.text

    async with db_session_maker() as session:
        dreams = (
            await session.scalars(select(Dream).where(Dream.user_id == fake_user.id))
        ).all()
    assert dreams[0].intensity == 7


async def test_safety_classify_receives_stage_and_context(
    client, fake_ai: FakeAiClient
) -> None:
    chat_id = await start_chat(client)

    fake_ai.queue_chat(_chat_response("recording", "Tell me more about the dream."))
    await client.post(f"/api/v1/chats/{chat_id}/message", json={"content": "A dream."})

    fake_ai.queue_chat(_chat_response("recording", "And then?"))
    await client.post(f"/api/v1/chats/{chat_id}/message", json={"content": "More."})

    safety_calls = [c for c in fake_ai.calls if c["method"] == "safety_classify"]
    assert safety_calls[-1]["payload"]["stage"] == "recording"
    assert safety_calls[-1]["payload"]["context"] == "Tell me more about the dream."


async def test_high_risk_returns_helpline_and_excludes_message_from_history(
    client, fake_ai: FakeAiClient
) -> None:
    chat_id = await start_chat(client)

    fake_ai.queue_chat(_chat_response("recording", "Tell me more."))
    await client.post(f"/api/v1/chats/{chat_id}/message", json={"content": "A dream."})

    fake_ai.queue_safety(
        {"risk": "high", "categories": ["suicidal_ideation"], "confidence": 0.9}
    )
    r = await client.post(
        f"/api/v1/chats/{chat_id}/message", json={"content": "flagged content"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ai_message"] is None
    assert body["safety_event"]["message"], "helpline text must be returned (ADR 0005)"
    assert body["stage"] == "recording", "IRT state must not advance"

    fake_ai.queue_chat(_chat_response("recording", "Go on."))
    await client.post(f"/api/v1/chats/{chat_id}/message", json={"content": "Go on."})

    last_chat_call = [c for c in fake_ai.calls if c["method"] == "chat"][-1]
    forwarded = [m["content"] for m in last_chat_call["payload"]["messages"]]
    assert "flagged content" not in forwarded, (
        "safety-flagged messages must not be replayed into AI history"
    )


async def test_ai_failure_removes_orphaned_user_message(
    client, fake_ai: FakeAiClient
) -> None:
    chat_id = await start_chat(client)

    fake_ai.queue_chat_error(AIServiceError("/chat failed: 503"))
    r = await client.post(
        f"/api/v1/chats/{chat_id}/message", json={"content": "lost turn"}
    )
    assert r.status_code == 502

    r = await client.get(f"/api/v1/chats/{chat_id}/messages")
    assert r.status_code == 200
    assert r.json() == [], "a turn that never got an AI reply must not linger in history"
