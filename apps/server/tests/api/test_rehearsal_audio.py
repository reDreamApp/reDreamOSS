"""Rehearsal audio: Storage purge service + the routes that use it.

`storage_service` talks to the Supabase Storage REST API over httpx; tests
install an `httpx.MockTransport` via the module's `_transport` seam and
assert the list/delete request shapes.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import httpx
import pytest

from redream_api.core.config import settings
from redream_api.services import storage_service
from redream_api.services.ai_client import AIServiceError


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


async def test_rehearsal_sends_owner_and_stores_url(client, fake_user, fake_ai, voice_on):
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
    }


async def test_rehearsal_provider_failure_is_502(client, fake_ai, voice_on, monkeypatch):
    dream_id = await _dream(client)

    async def fail(payload):
        raise AIServiceError("provider timeout")

    monkeypatch.setattr(fake_ai, "rehearsal_generate", fail)
    response = await client.post(f"/api/v1/dreams/{dream_id}/rehearsal", json={"language": "en"})
    assert response.status_code == 502


async def test_cached_rehearsal_is_reused_without_a_second_synthesis(
    client, fake_ai, fake_user, voice_on, monkeypatch
):
    from redream_api.api.v1.dreams import routes

    dream_id = await _dream(client, "x" * 1000)
    fake_ai.queue_rehearsal(
        {
            "audio_url": "https://storage.example/audio.mp3?token=t",
            "script": "x" * 1000,
            "duration_estimate": "1:00",
            "voice_id": "gb_jane_neutral",
            "cache_hit": False,
        }
    )
    url = f"/api/v1/dreams/{dream_id}/rehearsal"
    assert (await client.post(url, json={"language": "en"})).status_code == 200

    async def refresh(audio_url, user_id, requested_dream_id):
        assert user_id == fake_user.id and requested_dream_id == dream_id
        return audio_url

    monkeypatch.setattr(routes, "refresh_rehearsal_url", refresh)
    assert (await client.post(url, json={"language": "en"})).status_code == 200
    assert len(fake_ai.calls) == 1


_AUDIO = {
    "audio_url": "https://storage.example/audio.mp3?token=t",
    "script": "You find the path home.",
    "duration_estimate": "0:03",
    "voice_id": "gb_jane_neutral",
    "cache_hit": False,
}


async def test_rehearsal_while_speech_output_is_off_is_503_without_an_ai_call(client, fake_ai):
    fake_ai.stt_enabled = True  # only the output half counts here
    dream_id = await _dream(client)

    r = await client.post(f"/api/v1/dreams/{dream_id}/rehearsal", json={"language": "en"})

    assert r.status_code == 503
    assert r.json() == {"detail": "voice_unavailable"}
    assert [c for c in fake_ai.calls if c["method"] == "rehearsal_generate"] == []
    dream = (await client.get(f"/api/v1/dreams/{dream_id}")).json()
    assert dream["rehearsal_audio_url"] is None


async def test_rehearsal_off_checks_consent_and_ownership_first(client, fake_ai, monkeypatch):
    dream_id = await _dream(client)

    r = await client.post("/api/v1/dreams/999/rehearsal", json={"language": "en"})
    assert r.status_code == 404

    monkeypatch.setattr(settings, "consent_version", "2099-01-01")
    r = await client.post(f"/api/v1/dreams/{dream_id}/rehearsal", json={"language": "en"})
    assert r.status_code == 403
    assert r.json()["detail"] == "consent_required"


async def test_rehearsal_off_without_a_rewritten_dream_is_still_409(client, fake_ai):
    """The order: 404, 409, stored audio, then 503."""
    dream_id = await _dream(client, rewritten=None)

    r = await client.post(f"/api/v1/dreams/{dream_id}/rehearsal", json={"language": "en"})

    assert r.status_code == 409
    assert r.json()["detail"] == "rewritten_dream is required for rehearsal"
    assert [c for c in fake_ai.calls if c["method"] == "rehearsal_generate"] == []


async def test_stored_rehearsal_is_still_served_while_speech_output_is_off(
    client, fake_ai, voice_on, monkeypatch
):
    """Switching TTS off stops new audio, not playback of audio already
    stored: re-signing it sends nothing to a TTS provider."""
    from redream_api.api.v1.dreams import routes

    dream_id = await _dream(client)
    fake_ai.queue_rehearsal(_AUDIO)
    url = f"/api/v1/dreams/{dream_id}/rehearsal"
    assert (await client.post(url, json={"language": "en"})).status_code == 200

    async def refresh(audio_url, user_id, requested_dream_id):
        return audio_url

    monkeypatch.setattr(routes, "refresh_rehearsal_url", refresh)
    fake_ai.tts_enabled = False

    r = await client.post(url, json={"language": "en"})
    assert r.status_code == 200
    assert r.json()["rehearsal_audio_url"] == _AUDIO["audio_url"]
    # Another language would need new audio.
    r = await client.post(url, json={"language": "de"})
    assert r.status_code == 503
    assert r.json() == {"detail": "voice_unavailable"}
    assert len([c for c in fake_ai.calls if c["method"] == "rehearsal_generate"]) == 1


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
