"""Crisis-detection flow on POST /chats/{id}/message (ADR 0005)."""

from __future__ import annotations

import logging

import pytest
from sqlalchemy import select

from redream_api.core.config import HELPLINE_TEXT_EN_DEFAULT
from redream_api.db.models.chat_message import ChatMessage
from redream_api.services.ai_client import AIServiceError

from .conftest import FakeAiClient, start_chat


async def test_high_risk_input_skips_chat_and_flags_message(
    client, fake_ai: FakeAiClient, db_session_maker, caplog: pytest.LogCaptureFixture
) -> None:
    chat_id = await start_chat(client)
    caplog.set_level(logging.WARNING)

    fake_ai.queue_safety(
        {
            "risk": "high",
            "categories": ["suicidal_ideation"],
            "confidence": 0.92,
            "prompt_version": "safety-v0.1.0",
        }
    )

    r = await client.post(
        f"/api/v1/chats/{chat_id}/message",
        json={"content": "I don't want to be here anymore."},
    )
    assert r.status_code == 200, r.text

    body = r.json()
    assert body["ai_message"] is None, "AI should NOT be called on high-risk input"
    assert body["safety_event"] is not None
    assert body["safety_event"]["risk"] == "high"
    assert body["safety_event"]["categories"] == ["suicidal_ideation"]
    assert body["safety_event"]["confidence"] == 0.92
    # Helpline text must be returned (ADR 0005): HELPLINE_TEXT_EN, here its default.
    assert body["safety_event"]["message"] == HELPLINE_TEXT_EN_DEFAULT
    assert body["stage"] == "recording", "stage must NOT advance on high-risk"

    # Verify the AI service's /chat was not called.
    chat_calls = [c for c in fake_ai.calls if c["method"] == "chat"]
    assert chat_calls == []

    # One content-free warning: logs outlive account deletion, so no chat id,
    # user id, categories or message text.
    warnings = [
        rec.getMessage()
        for rec in caplog.records
        if rec.name.startswith("redream_api") and rec.levelno >= logging.WARNING
    ]
    assert warnings == ["crisis check: high risk"]

    # The user message should be persisted with safety_flagged=True.
    async with db_session_maker() as session:
        msgs = (
            await session.scalars(select(ChatMessage).where(ChatMessage.chat_id == chat_id))
        ).all()
    assert len(msgs) == 1
    assert msgs[0].safety_flagged is True


async def test_safety_failure_removes_orphaned_user_message(
    client, fake_ai: FakeAiClient
) -> None:
    """Parity with the chat-failure path: a 502 from the crisis check must not
    leave the user message behind to be replayed alongside the retry."""
    chat_id = await start_chat(client)

    fake_ai.queue_safety_error(AIServiceError("/safety/classify failed: 503"))
    r = await client.post(
        f"/api/v1/chats/{chat_id}/message", json={"content": "lost turn"}
    )
    assert r.status_code == 502
    assert r.json()["detail"].startswith("safety: ")
    assert all(c["method"] != "chat" for c in fake_ai.calls)

    r = await client.get(f"/api/v1/chats/{chat_id}/messages")
    assert r.status_code == 200
    assert r.json() == [], "a turn that failed the crisis check must not linger in history"


async def test_low_risk_input_proceeds_to_chat(client, fake_ai: FakeAiClient) -> None:
    chat_id = await start_chat(client)
    # Default safety queue is empty → returns risk=none, so this should pass through.
    fake_ai.queue_chat(
        {
            "stage": "recording",
            "response": "Tell me more about that dream.",
            "structured": None,
            "usage": {"input_tokens": 8, "output_tokens": 12},
            "prompt_version": "chat-v0.0.0-test",
        }
    )

    r = await client.post(
        f"/api/v1/chats/{chat_id}/message",
        json={"content": "I had a strange dream last night."},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["safety_event"] is None
    assert body["ai_message"]["content"] == "Tell me more about that dream."
    assert body["ai_message"]["role"] == "assistant"
    assert body["stage"] == "recording"


async def test_dev_flag_skips_classifier_entirely(
    client, fake_ai: FakeAiClient, monkeypatch
) -> None:
    """SAFETY_CLASSIFIER_ENABLED=false (dev-only): no /safety/classify call is
    made and the turn proceeds. A queued high-risk verdict proves the
    classifier was never consulted — not merely ignored."""
    from redream_api.core.config import settings

    monkeypatch.setattr(settings, "safety_classifier_enabled", False)
    chat_id = await start_chat(client)

    fake_ai.queue_safety(
        {
            "risk": "high",
            "categories": ["suicidal_ideation"],
            "confidence": 0.99,
            "prompt_version": "safety-v0.3.0",
        }
    )
    fake_ai.queue_chat(
        {
            "stage": "recording",
            "response": "Tell me more.",
            "structured": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "prompt_version": "test-v0",
        }
    )

    r = await client.post(f"/api/v1/chats/{chat_id}/message", json={"content": "who are you even"})

    assert r.status_code == 200, r.text
    assert r.json()["safety_event"] is None
    assert "safety_classify" not in [c["method"] for c in fake_ai.calls]
