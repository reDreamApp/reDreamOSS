"""DAILY_AI_REQUESTS_PER_USER: chat turns (both message routes) and
transcriptions per user per UTC day, counted at admission (core/limits).
Rehearsal renderings: test_rehearsal_audio.py.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException

from redream_api.core import limits
from redream_api.core.config import settings
from redream_api.services.ai_client import AIServiceError

from .conftest import FakeAiClient, start_chat

_REPLY = {
    "stage": "recording",
    "response": "Tell me more.",
    "structured": None,
    "usage": {"input_tokens": 1, "output_tokens": 1},
    "prompt_version": "test-v0",
}


def use(user_id, n: int) -> None:
    for _ in range(n):
        limits.count_ai_request(user_id)


def test_the_101st_request_of_a_utc_day_is_429_until_midnight(day):
    assert settings.daily_ai_requests_per_user == 100
    user_id = uuid4()
    use(user_id, 100)

    with pytest.raises(HTTPException) as exc:
        limits.count_ai_request(user_id)

    assert exc.value.status_code == 429
    assert exc.value.detail == "daily limit reached"
    assert exc.value.headers == {"Retry-After": str(2 * 3600)}
    limits.count_ai_request(uuid4())  # another user is not affected


def test_a_new_utc_day_starts_from_zero(day):
    user_id = uuid4()
    use(user_id, 100)

    day.now += timedelta(hours=2)

    use(user_id, 100)
    assert limits.daily_ai_requests._day == day.now.date()


def test_retry_after_is_at_least_a_second(day):
    user_id = uuid4()
    use(user_id, 100)
    day.now = datetime(2026, 10, 23, 23, 59, 59, 900_000, tzinfo=UTC)

    with pytest.raises(HTTPException) as exc:
        limits.count_ai_request(user_id)

    assert exc.value.headers == {"Retry-After": "1"}


def test_zero_switches_the_cap_off(monkeypatch, day):
    monkeypatch.setattr(settings, "daily_ai_requests_per_user", 0)

    use(uuid4(), 1000)


async def _messages(client, chat_id: int) -> list[str]:
    r = await client.get(f"/api/v1/chats/{chat_id}/messages")
    return [m["content"] for m in r.json()]


async def test_the_chat_turn_over_the_cap_is_429_and_stores_nothing(
    client, fake_ai: FakeAiClient, fake_user, day
):
    chat_id = await start_chat(client)
    use(fake_user.id, 99)
    fake_ai.queue_chat(_REPLY)

    assert (
        await client.post(f"/api/v1/chats/{chat_id}/message", json={"content": "100th"})
    ).status_code == 200
    calls = len(fake_ai.calls)

    r = await client.post(f"/api/v1/chats/{chat_id}/message", json={"content": "101st"})

    assert r.status_code == 429
    assert r.json() == {"detail": "daily limit reached"}
    assert r.headers["retry-after"] == str(2 * 3600)
    assert len(fake_ai.calls) == calls  # no crisis check, no chat call
    assert await _messages(client, chat_id) == ["100th", "Tell me more."]


async def test_the_streamed_chat_turn_over_the_cap_is_429_before_the_stream(
    client, fake_ai: FakeAiClient, fake_user, day
):
    chat_id = await start_chat(client)
    use(fake_user.id, 99)
    fake_ai.queue_chat_stream(
        [
            "data: " + json.dumps({"content": "Tell me more."}),
            "data: " + json.dumps({"final": _REPLY}),
            "data: [DONE]",
        ]
    )
    url = f"/api/v1/chats/{chat_id}/message/stream"

    assert (await client.post(url, json={"content": "100th"})).status_code == 200
    calls = len(fake_ai.calls)

    r = await client.post(url, json={"content": "101st"})

    assert r.status_code == 429
    assert r.headers["content-type"].startswith("application/json")
    assert r.json() == {"detail": "daily limit reached"}
    assert r.headers["retry-after"] == str(2 * 3600)
    assert len(fake_ai.calls) == calls
    assert await _messages(client, chat_id) == ["100th", "Tell me more."]


async def test_a_failed_chat_turn_still_counts(client, fake_ai: FakeAiClient, fake_user, day):
    chat_id = await start_chat(client)
    use(fake_user.id, 99)
    fake_ai.queue_chat_error(AIServiceError("down"))

    r = await client.post(f"/api/v1/chats/{chat_id}/message", json={"content": "hi"})
    assert r.status_code == 502

    r = await client.post(f"/api/v1/chats/{chat_id}/message", json={"content": "hi"})
    assert r.status_code == 429


async def test_a_turn_on_a_missing_chat_does_not_count(client, fake_user, day):
    use(fake_user.id, 99)

    r = await client.post("/api/v1/chats/999/message", json={"content": "hi"})

    assert r.status_code == 404
    use(fake_user.id, 1)  # the 100th is still free


def _upload():
    return {"file": ("dream.m4a", b"fake-audio", "audio/mp4")}


async def test_transcriptions_count(client, fake_ai: FakeAiClient, voice_on, fake_user, day):
    use(fake_user.id, 99)
    fake_ai.queue_transcribe({"text": "a forest", "language_detected": "en"})

    assert (await client.post("/api/v1/audio/transcribe", files=_upload())).status_code == 200

    r = await client.post("/api/v1/audio/transcribe", files=_upload())

    assert r.status_code == 429
    assert r.headers["retry-after"] == str(2 * 3600)
    assert [c["method"] for c in fake_ai.calls] == ["transcribe"]


async def test_a_transcription_while_speech_input_is_off_is_free(
    client, fake_ai: FakeAiClient, fake_user, day
):
    use(fake_user.id, 100)

    r = await client.post("/api/v1/audio/transcribe", files=_upload())

    assert r.status_code == 503
