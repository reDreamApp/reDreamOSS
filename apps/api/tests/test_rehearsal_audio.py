"""Rehearsal audio: Storage purge service + the routes that use it.

`storage_service` talks to the Supabase Storage REST API over httpx; tests
install an `httpx.MockTransport` via the module's `_transport` seam and
assert the list/delete request shapes.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import uuid4

import httpx
import pytest

from redream_api.core.config import settings
from redream_api.db.repositories.usage_repo import UsageRepo
from redream_api.services import storage_service
from redream_api.services.ai_client import AIServiceError

from .conftest import seed_usage


@pytest.fixture
def storage_settings(monkeypatch):
    monkeypatch.setattr(storage_service.settings, "supabase_url", "http://kong:8000")
    monkeypatch.setattr(storage_service.settings, "supabase_service_role_key", "srk")
    monkeypatch.setattr(storage_service.settings, "supabase_storage_bucket", "rehearsal-audio")


class _Bucket:
    """Folder tree → Storage list/delete responses."""

    def __init__(self, files: list[str], *, fail_delete: bool = False) -> None:
        self.files = set(files)
        self.deleted: list[str] = []
        self.fail_delete = fail_delete

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["apikey"] == "srk"
        body = json.loads(request.content)
        if request.method == "POST":
            assert request.url.path == "/storage/v1/object/list/rehearsal-audio"
            prefix = body["prefix"] + "/"
            children: dict[str, bool] = {}
            for path in self.files:
                if path.startswith(prefix):
                    name, _, rest = path[len(prefix) :].partition("/")
                    children[name] = children.get(name, False) or bool(rest)
            return httpx.Response(
                200,
                json=[
                    {"name": n, "id": None if is_folder else f"id-{n}"}
                    for n, is_folder in children.items()
                ],
            )
        assert request.method == "DELETE"
        assert request.url.path == "/storage/v1/object/rehearsal-audio"
        if self.fail_delete:
            return httpx.Response(500, text="boom")
        self.deleted += body["prefixes"]
        return httpx.Response(200, json=[])


def _install(monkeypatch, bucket: _Bucket) -> _Bucket:
    monkeypatch.setattr(storage_service, "_transport", httpx.MockTransport(bucket))
    return bucket


async def test_user_purge_deletes_every_dream_folder(monkeypatch, storage_settings):
    user = uuid4()
    other = uuid4()
    bucket = _install(
        monkeypatch,
        _Bucket(
            [
                f"users/{user}/1/en-a.mp3",
                f"users/{user}/1/de-b.mp3",
                f"users/{user}/2/en-c.mp3",
                f"users/{other}/3/en-d.mp3",
            ]
        ),
    )

    assert await storage_service.delete_rehearsal_audio_for_user(user) == 3
    assert sorted(bucket.deleted) == sorted(
        [f"users/{user}/1/en-a.mp3", f"users/{user}/1/de-b.mp3", f"users/{user}/2/en-c.mp3"]
    )


async def test_dream_purge_only_touches_that_dream(monkeypatch, storage_settings):
    user = uuid4()
    bucket = _install(
        monkeypatch, _Bucket([f"users/{user}/1/en-a.mp3", f"users/{user}/2/en-c.mp3"])
    )

    assert await storage_service.delete_rehearsal_audio_for_dream(user, 1) == 1
    assert bucket.deleted == [f"users/{user}/1/en-a.mp3"]


async def test_nothing_stored_sends_no_delete(monkeypatch, storage_settings):
    bucket = _install(monkeypatch, _Bucket([]))
    assert await storage_service.delete_rehearsal_audio_for_user(uuid4()) == 0
    assert bucket.deleted == []


async def test_storage_errors_are_logged_not_raised(monkeypatch, storage_settings):
    user = uuid4()
    _install(monkeypatch, _Bucket([f"users/{user}/1/en-a.mp3"], fail_delete=True))
    assert await storage_service.delete_rehearsal_audio_for_user(user) == 0


async def test_unconfigured_storage_is_skipped(monkeypatch):
    monkeypatch.setattr(storage_service.settings, "supabase_service_role_key", "")
    assert await storage_service.delete_rehearsal_audio_for_user(uuid4()) == 0


async def _dream(client, rewritten: str | None = "You find the path home.") -> int:
    r = await client.post(
        "/api/v1/dreams",
        json={
            "title": "Forest",
            "original_dream": "Lost in a forest.",
            "rewritten_dream": rewritten,
            "is_nightmare": True,
        },
    )
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


async def test_rehearsal_sends_owner_and_stores_url(client, fake_user, fake_ai):
    dream_id = await _dream(client)
    fake_ai.queue_rehearsal(
        {
            "audio_url": "https://supabase.example.com/storage/v1/object/sign/x.mp3?token=t",
            "script": "You find the path home.",
            "duration_estimate": "0:03",
            "voice_id": "gb_jane_neutral",
            "cache_hit": False,
        }
    )

    r = await client.post(f"/api/v1/dreams/{dream_id}/rehearsal", json={"language": "en"})

    assert r.status_code == 200, r.text
    assert r.json()["rehearsal_audio_url"].endswith("x.mp3?token=t")
    assert r.json()["rehearsal_duration"] == "0:03"
    (call,) = [c for c in fake_ai.calls if c["method"] == "rehearsal_generate"]
    assert call["payload"] == {
        "dream_id": str(dream_id),
        "user_id": str(fake_user.id),
        "rewritten_dream": "You find the path home.",
        "language": "en",
        "max_synthesis_attempts": 2,
    }


def _rehearsal_result(**usage: Any) -> dict[str, Any]:
    return {
        "audio_url": "https://storage.example/audio.mp3?token=t",
        "script": "x" * 1000,
        "duration_estimate": "1:00",
        "voice_id": "gb_jane_neutral",
        "cache_hit": False,
        **usage,
    }


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        ({"synthesis_attempts": 1}, 8000),
        ({"synthesis_attempts": 2}, 16000),
        ({"synthesis_attempts": 0, "cache_hit": True}, 0),
        ({}, 16000),  # An older AI service: no usage means no refund.
        ({"synthesis_attempts": 0}, 16000),
        ({"synthesis_attempts": -1}, 16000),
        ({"synthesis_attempts": 3}, 16000),
        ({"synthesis_attempts": True}, 16000),
    ],
)
async def test_tts_settles_weighted_usage(
    client, fake_ai, fake_user, db_session_maker, monkeypatch, usage, expected
):
    monkeypatch.setattr(settings, "tts_budget_tokens_per_character", 8)
    dream_id = await _dream(client, "  " + "x" * 1000 + "  ")
    fake_ai.queue_rehearsal(_rehearsal_result(**usage))
    response = await client.post(f"/api/v1/dreams/{dream_id}/rehearsal", json={"language": "en"})
    assert response.status_code == 200, response.text
    async with db_session_maker() as db:
        assert await UsageRepo(db).get_today_tokens(fake_user.id) == expected
        assert await UsageRepo(db).get_today_total() == expected


@pytest.mark.parametrize("limit", ["user", "total"])
async def test_tts_rejects_before_ai_without_room_for_both_attempts(
    client, fake_ai, fake_user, db_session_maker, monkeypatch, limit
):
    monkeypatch.setattr(settings, "tts_budget_tokens_per_character", 8)
    setting = "daily_llm_token_budget" if limit == "user" else "daily_llm_token_budget_total"
    monkeypatch.setattr(settings, setting, 15999)
    dream_id = await _dream(client, "x" * 1000)
    response = await client.post(f"/api/v1/dreams/{dream_id}/rehearsal", json={"language": "en"})
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0
    assert fake_ai.calls == []
    async with db_session_maker() as db:
        assert await UsageRepo(db).get_today_tokens(fake_user.id) == 0
        assert await UsageRepo(db).get_today_total() == 0


async def test_tts_shares_chat_budget_and_returns_reserve_on_failure(
    client, fake_ai, fake_user, db_session_maker, monkeypatch
):
    monkeypatch.setattr(settings, "tts_budget_tokens_per_character", 8)
    monkeypatch.setattr(settings, "daily_llm_token_budget", 20000)
    await seed_usage(db_session_maker, user_id=fake_user.id, tokens=4000)
    dream_id = await _dream(client, "x" * 1000)

    async def fail(payload):
        raise AIServiceError("provider timeout")

    monkeypatch.setattr(fake_ai, "rehearsal_generate", fail)
    response = await client.post(f"/api/v1/dreams/{dream_id}/rehearsal", json={"language": "en"})
    assert response.status_code == 502
    # The provider failed: what was reserved for both attempts is given back,
    # so an outage does not use up the day.
    async with db_session_maker() as db:
        assert await UsageRepo(db).get_today_tokens(fake_user.id) == 4000
        assert await UsageRepo(db).get_today_total() == 0
    response = await client.post(f"/api/v1/dreams/{dream_id}/rehearsal", json={"language": "en"})
    assert response.status_code == 502


async def test_cached_rehearsal_remains_available_at_budget_limit(
    client, fake_ai, fake_user, db_session_maker, monkeypatch
):
    from redream_api.api.v1.dreams import routes

    monkeypatch.setattr(settings, "tts_budget_tokens_per_character", 8)
    dream_id = await _dream(client, "x" * 1000)
    fake_ai.queue_rehearsal(_rehearsal_result(synthesis_attempts=1))
    url = f"/api/v1/dreams/{dream_id}/rehearsal"
    assert (await client.post(url, json={"language": "en"})).status_code == 200
    monkeypatch.setattr(settings, "daily_llm_token_budget", 8000)
    monkeypatch.setattr(settings, "daily_llm_token_budget_total", 8000)

    async def refresh(audio_url, user_id, requested_dream_id):
        assert user_id == fake_user.id and requested_dream_id == dream_id
        return audio_url

    monkeypatch.setattr(routes, "refresh_rehearsal_url", refresh)
    assert (await client.post(url, json={"language": "en"})).status_code == 200
    assert len(fake_ai.calls) == 1
    async with db_session_maker() as db:
        assert await UsageRepo(db).get_today_total() == 8000


async def test_concurrent_tts_cannot_spend_reserved_capacity(
    client, fake_ai, fake_user, db_session_maker, monkeypatch
):
    monkeypatch.setattr(settings, "tts_budget_tokens_per_character", 8)
    monkeypatch.setattr(settings, "daily_llm_token_budget_total", 16000)
    dream_id = await _dream(client, "x" * 1000)
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def synthesize(payload):
        calls.append(payload)
        started.set()
        await release.wait()
        return _rehearsal_result(synthesis_attempts=1)

    monkeypatch.setattr(fake_ai, "rehearsal_generate", synthesize)
    url = f"/api/v1/dreams/{dream_id}/rehearsal"
    first = asyncio.create_task(client.post(url, json={"language": "en"}))
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        others = await asyncio.gather(
            *[client.post(url, json={"language": "en"}) for _ in range(5)]
        )
        assert all(response.status_code == 429 for response in others)
        assert len(calls) == 1
    finally:
        release.set()
        response = await first
    assert response.status_code == 200
    async with db_session_maker() as db:
        assert await UsageRepo(db).get_today_tokens(fake_user.id) == 8000
        assert await UsageRepo(db).get_today_total() == 8000


async def test_deleting_a_dream_deletes_its_audio(client, fake_user, monkeypatch):
    from redream_api.api.v1.dreams import routes as dream_routes

    calls: list[Any] = []

    async def fake_purge(user_id, dream_id):
        calls.append((user_id, dream_id))
        return 1

    monkeypatch.setattr(dream_routes, "delete_rehearsal_audio_for_dream", fake_purge)
    dream_id = await _dream(client)

    r = await client.delete(f"/api/v1/dreams/{dream_id}")

    assert r.status_code == 204
    assert calls == [(fake_user.id, dream_id)]


async def test_account_deletion_purges_storage_and_login(client, fake_user, monkeypatch):
    from redream_api.api.v1.users import routes as user_routes

    calls: dict[str, Any] = {}

    async def fake_storage(user_id):
        calls["storage"] = user_id

    async def fake_login(user_id):
        calls["login"] = user_id
        return True

    monkeypatch.setattr(user_routes, "delete_rehearsal_audio_for_user", fake_storage)
    monkeypatch.setattr(user_routes, "delete_login", fake_login)

    r = await client.delete("/api/v1/users/me")

    assert r.status_code == 204
    assert calls == {"storage": fake_user.id, "login": fake_user.id}
