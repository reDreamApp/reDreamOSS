"""Summary-stage flow: structured AI response → Dream upserted (ADR 0003)."""

from __future__ import annotations

from sqlalchemy import select

from redream_api.db.models.dream import Dream

from .conftest import FakeAiClient, start_chat


async def test_summary_stage_creates_dream_from_structured(
    client, fake_ai: FakeAiClient, fake_user, db_session_maker
) -> None:
    chat_id = await start_chat(client)

    fake_ai.queue_chat(
        {
            "stage": "summary",
            "response": "Here is your dream summary.",
            "structured": {
                "title": "Walking the bright forest",
                "original_summary": "You are pursued by a shadowy figure through a dark forest.",
                "rewritten_summary": "You walk through a sunlit forest and find a calm clearing.",
                "dream_summary": "Original-and-rewritten dream synthesis.",
            },
            "usage": {"input_tokens": 100, "output_tokens": 200},
            "prompt_version": "prompts-en-v1.0.0",
        }
    )

    r = await client.post(
        f"/api/v1/chats/{chat_id}/message",
        json={"content": "I'm satisfied with the rewritten dream."},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stage"] == "summary"
    assert body["safety_event"] is None
    assert body["ai_message"]["stage"] == "summary"

    async with db_session_maker() as session:
        dreams = (
            await session.scalars(
                select(Dream).where(Dream.user_id == fake_user.id)
            )
        ).all()

    assert len(dreams) == 1, "summary stage with structured payload must create a Dream"
    d = dreams[0]
    assert d.title == "Walking the bright forest"
    assert "sunlit forest" in d.rewritten_dream
    assert d.session_id == str(chat_id)
    assert d.source == "chatbot"
    assert d.is_nightmare is True


async def test_second_summary_updates_existing_dream(
    client, fake_ai: FakeAiClient, fake_user, db_session_maker
) -> None:
    """A second summary turn for the same chat updates, not duplicates, the Dream."""
    chat_id = await start_chat(client)

    fake_ai.queue_chat(
        {
            "stage": "summary",
            "response": "Summary v1",
            "structured": {
                "title": "Title v1",
                "original_summary": "Original v1",
                "rewritten_summary": "Rewritten v1",
                "dream_summary": "Synthesis v1",
            },
            "usage": {"input_tokens": 10, "output_tokens": 10},
            "prompt_version": "prompts-en-v1.0.0",
        }
    )
    await client.post(f"/api/v1/chats/{chat_id}/message", json={"content": "ok"})

    fake_ai.queue_chat(
        {
            "stage": "summary",
            "response": "Summary v2",
            "structured": {
                "title": "Title v2",
                "original_summary": "Original v2",
                "rewritten_summary": "Rewritten v2",
                "dream_summary": "Synthesis v2",
            },
            "usage": {"input_tokens": 10, "output_tokens": 10},
            "prompt_version": "prompts-en-v1.0.0",
        }
    )
    await client.post(
        f"/api/v1/chats/{chat_id}/message", json={"content": "I want to revise"}
    )

    async with db_session_maker() as session:
        dreams = (
            await session.scalars(
                select(Dream).where(Dream.user_id == fake_user.id)
            )
        ).all()

    assert len(dreams) == 1, "subsequent summaries should update, not duplicate"
    assert dreams[0].title == "Title v2"
    assert "Rewritten v2" in dreams[0].rewritten_dream
