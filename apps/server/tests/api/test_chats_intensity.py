"""PUT /chats/{id}/intensity — app-owned intensity rating on the chat's Dream.

The routed chat no longer elicits the 0-10 rating (no `intensity` stage), so
the client collects it at session end and attaches it via this endpoint.
"""

from __future__ import annotations

from sqlalchemy import select

from redream_api.db.models.dream import Dream

from .conftest import FakeAiClient, start_chat


def _summary_response(title: str = "A dream") -> dict:
    return {
        "stage": "summary",
        "response": "Here is your dream summary.",
        "structured": {
            "title": title,
            "original_summary": "Original",
            "rewritten_summary": "Rewritten",
            "dream_summary": "Synthesis",
        },
        "usage": {"input_tokens": 10, "output_tokens": 10},
        "prompt_version": "prompts-en-v1.0.0",
    }


async def test_set_intensity_updates_chat_dream(
    client, fake_ai: FakeAiClient, fake_user, db_session_maker
) -> None:
    chat_id = await start_chat(client)
    fake_ai.queue_chat(_summary_response())
    await client.post(f"/api/v1/chats/{chat_id}/message", json={"content": "ok"})

    r = await client.put(
        f"/api/v1/chats/{chat_id}/intensity", json={"intensity": 7}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["chat_id"] == chat_id
    assert body["intensity"] == 7

    async with db_session_maker() as session:
        dream = (
            await session.scalars(select(Dream).where(Dream.user_id == fake_user.id))
        ).one()
    assert dream.id == body["dream_id"]
    assert dream.intensity == 7


async def test_set_intensity_does_not_invalidate_rehearsal_audio(
    client, fake_ai: FakeAiClient, fake_user, db_session_maker
) -> None:
    """Unlike a summary re-upsert, the rating leaves cached rehearsal intact."""
    chat_id = await start_chat(client)
    fake_ai.queue_chat(_summary_response())
    await client.post(f"/api/v1/chats/{chat_id}/message", json={"content": "ok"})

    async with db_session_maker() as session:
        dream = (
            await session.scalars(select(Dream).where(Dream.user_id == fake_user.id))
        ).one()
        dream.rehearsal_audio_key = f"users/{fake_user.id}/{dream.id}/en-{'a' * 24}.mp3"
        dream.rehearsal_script = "script"
        session.add(dream)
        await session.commit()

    r = await client.put(
        f"/api/v1/chats/{chat_id}/intensity", json={"intensity": 3}
    )
    assert r.status_code == 200, r.text

    async with db_session_maker() as session:
        dream = (
            await session.scalars(select(Dream).where(Dream.user_id == fake_user.id))
        ).one()
    assert dream.intensity == 3
    assert dream.rehearsal_audio_key == f"users/{fake_user.id}/{dream.id}/en-{'a' * 24}.mp3"
    assert dream.rehearsal_script == "script"


async def test_set_intensity_404_when_chat_missing(client) -> None:
    r = await client.put("/api/v1/chats/999/intensity", json={"intensity": 5})
    assert r.status_code == 404


async def test_set_intensity_404_before_summary_created_dream(client) -> None:
    chat_id = await start_chat(client)
    r = await client.put(
        f"/api/v1/chats/{chat_id}/intensity", json={"intensity": 5}
    )
    assert r.status_code == 404


async def test_set_intensity_rejects_out_of_range(client, fake_ai: FakeAiClient) -> None:
    chat_id = await start_chat(client)
    fake_ai.queue_chat(_summary_response())
    await client.post(f"/api/v1/chats/{chat_id}/message", json={"content": "ok"})

    for bad in (-1, 11):
        r = await client.put(
            f"/api/v1/chats/{chat_id}/intensity", json={"intensity": bad}
        )
        assert r.status_code == 422, r.text
