"""Rehearsal audio: generation, the cache, signed links, resets and deletion.

The `synth` fixture runs the real AI side (`redream_ai.api.audio` → TTS → the
local store) behind the FakeAiClient, with only the speech provider faked, so
the MP3s land in this test's AUDIO_DIR (conftest's `audio_dir`).
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from redream_ai.audio import store
from redream_api.core import limits
from redream_api.core.config import settings
from redream_api.db.models.dream import Dream
from redream_api.db.repositories.user_repo import UserRepo
from redream_api.services import audio_links
from redream_api.services.ai_client import AIServiceError

from .conftest import FakeAiClient, start_chat


@pytest.fixture
def synth(monkeypatch, fake_ai: FakeAiClient, voice_on) -> list[str]:
    """Speech on, the real AI rehearsal path; returns the scripts rendered."""
    from redream_ai.api import audio as ai_audio
    from redream_ai.audio import tts
    from redream_ai.models import RehearsalRequest

    for name, value in {
        "tts_base_url": "https://tts.example.test/v1",
        "tts_api_key": "k",
        "tts_model": "tts-model",
        "tts_voice": "marin",
        "tts_voice_en": "",
        "tts_voice_de": "",
    }.items():
        monkeypatch.setattr(tts.settings, name, value)
    rendered: list[str] = []

    async def synthesize(script, language):
        rendered.append(script)
        return f"ID3 {language.value} {script}".encode()

    async def rehearsal_generate(payload: dict[str, Any]) -> dict[str, Any]:
        fake_ai.calls.append({"method": "rehearsal_generate", "payload": payload})
        resp = await ai_audio.rehearsal_generate(RehearsalRequest.model_validate(payload))
        return resp.model_dump(mode="json")

    monkeypatch.setattr(tts, "synthesize", synthesize)
    monkeypatch.setattr(fake_ai, "rehearsal_generate", rehearsal_generate)
    return rendered


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


async def _rehearse(client, dream_id: int, language: str = "en") -> dict[str, Any]:
    r = await client.post(f"/api/v1/dreams/{dream_id}/rehearsal", json={"language": language})
    assert r.status_code == 200, r.text
    return dict(r.json())


def _key_of(link: str) -> str:
    return urlsplit(link).path.removeprefix(audio_links.FILES_PATH)


def _files(audio_dir: Path) -> set[str]:
    if not audio_dir.exists():
        return set()
    return {p.relative_to(audio_dir).as_posix() for p in audio_dir.rglob("*") if p.is_file()}


def _dream_folder(audio_dir: Path, user_id: UUID, dream_id: int) -> Path:
    return audio_dir / "users" / str(user_id) / str(dream_id)


async def _row(db_session_maker, dream_id: int) -> Dream | None:
    async with db_session_maker() as session:
        return (await session.scalars(select(Dream).where(Dream.id == dream_id))).first()


_REHEARSAL_FIELDS = (
    "rehearsal_audio_url",
    "rehearsal_script",
    "rehearsal_duration",
    "rehearsal_language",
    "rehearsal_generated_at",
)


# --- generation + the file route ---------------------------------------------------


async def test_new_rehearsal_writes_the_file_and_serves_it_by_signed_link(
    client, fake_user, fake_ai, synth, audio_dir, db_session_maker
):
    dream_id = await _dream(client)

    dream = await _rehearse(client, dream_id)

    link = dream["rehearsal_audio_url"]
    assert link.startswith(f"/api/v1/audio/files/users/{fake_user.id}/{dream_id}/en-")
    query = parse_qs(urlsplit(link).query)
    assert set(query) == {"exp", "sig"}
    key = _key_of(link)
    assert _files(audio_dir) == {key}
    assert dream["rehearsal_script"] == "You find the path home."
    assert dream["rehearsal_language"] == "en"
    # The row keeps the key, not a link.
    row = await _row(db_session_maker, dream_id)
    assert row is not None and row.rehearsal_audio_key == key
    (call,) = [c for c in fake_ai.calls if c["method"] == "rehearsal_generate"]
    assert call["payload"] == {
        "dream_id": str(dream_id),
        "user_id": str(fake_user.id),
        "rewritten_dream": "You find the path home.",
        "language": "en",
    }

    r = await client.get(link)
    assert r.status_code == 200
    assert r.content == b"ID3 en You find the path home."
    assert r.headers["content-type"] == "audio/mpeg"


async def test_regeneration_removes_the_older_file_of_that_language(
    client, fake_user, synth, audio_dir, monkeypatch
):
    from redream_ai.audio import tts

    dream_id = await _dream(client)
    first_en = _key_of((await _rehearse(client, dream_id, "en"))["rehearsal_audio_url"])
    de = _key_of((await _rehearse(client, dream_id, "de"))["rehearsal_audio_url"])
    monkeypatch.setattr(tts.settings, "tts_voice", "cedar")  # a new voice, a new file

    second_en = _key_of((await _rehearse(client, dream_id, "en"))["rehearsal_audio_url"])

    assert second_en != first_en
    assert _files(audio_dir) == {de, second_en}
    assert len(synth) == 3


async def test_cache_hit_with_the_file_sends_nothing_to_tts(client, fake_ai, synth):
    dream_id = await _dream(client)
    first = await _rehearse(client, dream_id)

    second = await _rehearse(client, dream_id)

    assert _key_of(second["rehearsal_audio_url"]) == _key_of(first["rehearsal_audio_url"])
    assert len(synth) == 1
    assert len([c for c in fake_ai.calls if c["method"] == "rehearsal_generate"]) == 1


async def test_stored_rehearsal_is_still_served_while_speech_output_is_off(client, fake_ai, synth):
    """Switching TTS off stops new audio, not playback of audio already
    stored: a fresh link to it sends nothing to a TTS provider."""
    dream_id = await _dream(client)
    first = await _rehearse(client, dream_id)
    fake_ai.tts_enabled = False

    again = await _rehearse(client, dream_id)

    assert _key_of(again["rehearsal_audio_url"]) == _key_of(first["rehearsal_audio_url"])
    assert (await client.get(again["rehearsal_audio_url"])).status_code == 200
    # Another language would need new audio.
    r = await client.post(f"/api/v1/dreams/{dream_id}/rehearsal", json={"language": "de"})
    assert r.status_code == 503
    assert r.json() == {"detail": "voice_unavailable"}
    assert len([c for c in fake_ai.calls if c["method"] == "rehearsal_generate"]) == 1


async def test_a_missing_file_is_forgotten_and_rendered_again(client, fake_user, synth, audio_dir):
    dream_id = await _dream(client)
    key = _key_of((await _rehearse(client, dream_id))["rehearsal_audio_url"])
    (audio_dir / key).unlink()

    dream = await _rehearse(client, dream_id)

    assert _key_of(dream["rehearsal_audio_url"]) == key
    assert _files(audio_dir) == {key}
    assert len(synth) == 2


async def test_a_missing_file_while_speech_output_is_off_is_cleared_then_503(
    client, fake_ai, synth, audio_dir
):
    dream_id = await _dream(client)
    key = _key_of((await _rehearse(client, dream_id))["rehearsal_audio_url"])
    (audio_dir / key).unlink()
    fake_ai.tts_enabled = False

    r = await client.post(f"/api/v1/dreams/{dream_id}/rehearsal", json={"language": "en"})

    assert r.status_code == 503
    assert r.json() == {"detail": "voice_unavailable"}
    dream = (await client.get(f"/api/v1/dreams/{dream_id}")).json()
    assert all(dream[field] is None for field in _REHEARSAL_FIELDS)


async def test_a_key_for_another_dream_or_user_is_refused(
    client, fake_user, fake_ai, voice_on, db_session_maker
):
    dream_id = await _dream(client)
    for key in (
        store.key_for(fake_user.id, dream_id + 1, "en", "a" * 24),
        store.key_for(uuid4(), dream_id, "en", "a" * 24),
        "https://storage.example/audio.mp3",
    ):
        fake_ai.queue_rehearsal(
            {
                "audio_key": key,
                "script": "s",
                "duration_estimate": "0:01",
                "voice_id": "v",
                "cache_hit": False,
            }
        )
        r = await client.post(f"/api/v1/dreams/{dream_id}/rehearsal", json={"language": "en"})
        assert r.status_code == 502
        row = await _row(db_session_maker, dream_id)
        assert row is not None and row.rehearsal_audio_key is None


async def test_rehearsal_provider_failure_is_502(client, fake_ai, voice_on, monkeypatch):
    dream_id = await _dream(client)

    async def fail(payload):
        raise AIServiceError("provider timeout")

    monkeypatch.setattr(fake_ai, "rehearsal_generate", fail)
    response = await client.post(f"/api/v1/dreams/{dream_id}/rehearsal", json={"language": "en"})
    assert response.status_code == 502


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


@pytest.mark.parametrize("field", ["rehearsal_audio_url", "rehearsal_audio_key"])
async def test_the_audio_fields_are_not_accepted_on_input(client, field):
    body = {"title": "x", "original_dream": "y", field: "users/x.mp3"}
    assert (await client.post("/api/v1/dreams", json=body)).status_code == 422
    dream_id = await _dream(client)
    r = await client.put(f"/api/v1/dreams/{dream_id}", json={field: "users/x.mp3"})
    assert r.status_code == 422


# --- GET /dreams/{id}/rehearsal-url --------------------------------------------------


async def test_rehearsal_url_returns_a_fresh_link(client, synth):
    dream_id = await _dream(client)
    first = (await _rehearse(client, dream_id))["rehearsal_audio_url"]

    before = int(time.time())
    r = await client.get(f"/api/v1/dreams/{dream_id}/rehearsal-url")

    assert r.status_code == 200
    link = r.json()["rehearsal_audio_url"]
    assert _key_of(link) == _key_of(first)
    (exp,) = parse_qs(urlsplit(link).query)["exp"]
    assert before + 300 <= int(exp) <= int(time.time()) + 300
    assert (await client.get(link)).status_code == 200


async def test_rehearsal_url_is_404_and_cleared_when_the_file_is_gone(
    client, synth, audio_dir, db_session_maker
):
    dream_id = await _dream(client)
    key = _key_of((await _rehearse(client, dream_id))["rehearsal_audio_url"])
    (audio_dir / key).unlink()

    r = await client.get(f"/api/v1/dreams/{dream_id}/rehearsal-url")

    assert r.status_code == 404
    assert r.json() == {"detail": "no rehearsal audio"}
    row = await _row(db_session_maker, dream_id)
    assert row is not None
    assert row.rehearsal_audio_key is None and row.rehearsal_generated_at is None


async def test_a_file_that_cannot_be_checked_is_503_and_keeps_the_key(
    client, fake_ai, synth, monkeypatch, db_session_maker
):
    dream_id = await _dream(client)
    key = _key_of((await _rehearse(client, dream_id))["rehearsal_audio_url"])
    calls = len(fake_ai.calls)

    def broken(key):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(store, "exists", broken)
    for r in (
        await client.get(f"/api/v1/dreams/{dream_id}/rehearsal-url"),
        await client.post(f"/api/v1/dreams/{dream_id}/rehearsal", json={"language": "en"}),
    ):
        assert r.status_code == 503
        assert r.json() == {"detail": "audio could not be read, try again"}

    assert len(fake_ai.calls) == calls
    row = await _row(db_session_maker, dream_id)
    assert row is not None and row.rehearsal_audio_key == key


async def test_rehearsal_url_without_audio_is_404(client):
    dream_id = await _dream(client)
    r = await client.get(f"/api/v1/dreams/{dream_id}/rehearsal-url")
    assert r.status_code == 404
    assert r.json() == {"detail": "no rehearsal audio"}
    assert (await client.get("/api/v1/dreams/999/rehearsal-url")).status_code == 404


# --- resets: a changed rewritten dream drops its audio ----------------------------


async def test_editing_the_rewritten_dream_deletes_its_audio(client, fake_user, synth, audio_dir):
    dream_id = await _dream(client)
    await _rehearse(client, dream_id)
    folder = _dream_folder(audio_dir, fake_user.id, dream_id)

    r = await client.put(f"/api/v1/dreams/{dream_id}", json={"title": "Renamed"})
    assert r.status_code == 200
    assert r.json()["rehearsal_audio_url"] is not None  # unchanged text keeps its audio
    assert folder.is_dir()

    r = await client.put(f"/api/v1/dreams/{dream_id}", json={"rewritten_dream": "A new ending."})

    assert r.status_code == 200
    assert all(r.json()[field] is None for field in _REHEARSAL_FIELDS)
    assert not folder.exists()


async def test_a_failed_reset_deletion_is_logged_not_raised(
    client, fake_user, synth, audio_dir, monkeypatch, caplog
):
    dream_id = await _dream(client)
    await _rehearse(client, dream_id)

    def refuse(user_id, dream_id):
        raise PermissionError(13, "Permission denied", str(audio_dir))

    monkeypatch.setattr(store, "delete_dream", refuse)
    with caplog.at_level(logging.ERROR):
        r = await client.put(f"/api/v1/dreams/{dream_id}", json={"rewritten_dream": "New."})

    assert r.status_code == 200
    assert r.json()["rehearsal_audio_url"] is None
    (record,) = [rec for rec in caplog.records if rec.levelno == logging.ERROR]
    assert str(audio_dir) not in record.getMessage()
    assert str(fake_user.id) not in record.getMessage()


async def test_a_new_chat_summary_deletes_the_dreams_audio(
    client, fake_user, fake_ai, synth, audio_dir, db_session_maker
):
    chat_id = await start_chat(client)

    def summary(version: str) -> dict[str, Any]:
        return {
            "stage": "summary",
            "response": f"Summary {version}",
            "structured": {
                "title": f"Title {version}",
                "original_summary": f"Original {version}",
                "rewritten_summary": f"Rewritten {version}",
                "dream_summary": f"Synthesis {version}",
            },
            "usage": {"input_tokens": 10, "output_tokens": 10},
            "prompt_version": "prompts-en-v1.0.0",
        }

    fake_ai.queue_chat(summary("v1"))
    await client.post(f"/api/v1/chats/{chat_id}/message", json={"content": "ok"})
    async with db_session_maker() as session:
        (dream,) = (await session.scalars(select(Dream))).all()
    await _rehearse(client, dream.id)
    folder = _dream_folder(audio_dir, fake_user.id, dream.id)
    assert folder.is_dir()

    fake_ai.queue_chat(summary("v2"))
    await client.post(f"/api/v1/chats/{chat_id}/message", json={"content": "revise"})

    assert not folder.exists()
    row = await _row(db_session_maker, dream.id)
    assert row is not None
    assert row.rehearsal_audio_key is None and row.rehearsal_script is None


# --- deletion: the folder before the rows --------------------------------------------


async def test_deleting_a_dream_deletes_its_folder_before_the_row(
    client, fake_user, synth, audio_dir, db_session_maker, monkeypatch
):
    from redream_api.api.v1.dreams import routes as dream_routes

    dream_id = await _dream(client)
    other_id = await _dream(client)
    await _rehearse(client, dream_id)
    await _rehearse(client, other_id)
    real = dream_routes.delete_dream_audio
    rows_at_deletion: list[bool] = []

    async def recording(user_id, requested_id):
        rows_at_deletion.append(await _row(db_session_maker, requested_id) is not None)
        await real(user_id, requested_id)

    monkeypatch.setattr(dream_routes, "delete_dream_audio", recording)

    r = await client.delete(f"/api/v1/dreams/{dream_id}")

    assert r.status_code == 204
    assert rows_at_deletion == [True]
    assert not _dream_folder(audio_dir, fake_user.id, dream_id).exists()
    assert _dream_folder(audio_dir, fake_user.id, other_id).is_dir()
    assert await _row(db_session_maker, dream_id) is None


async def test_a_dream_without_audio_is_deleted(client):
    dream_id = await _dream(client)
    assert (await client.delete(f"/api/v1/dreams/{dream_id}")).status_code == 204


async def test_a_failed_folder_deletion_keeps_the_dream(
    client, synth, db_session_maker, monkeypatch
):
    dream_id = await _dream(client)
    await _rehearse(client, dream_id)

    def refuse(user_id, dream_id):
        raise OSError("busy")

    monkeypatch.setattr(store, "delete_dream", refuse)

    r = await client.delete(f"/api/v1/dreams/{dream_id}")

    assert r.status_code == 503
    assert r.json() == {"detail": "audio could not be deleted, try again"}
    assert await _row(db_session_maker, dream_id) is not None


async def test_account_deletion_removes_the_user_folder(
    client, fake_user, synth, audio_dir, db_session_maker
):
    other = store.key_for(uuid4(), 1, "en", "a" * 24)
    store.write(other, b"someone else's")
    await _rehearse(client, await _dream(client))
    await _rehearse(client, await _dream(client), "de")

    r = await client.delete("/api/v1/users/me")

    assert r.status_code == 204
    assert not (audio_dir / "users" / str(fake_user.id)).exists()
    assert _files(audio_dir) == {other}
    async with db_session_maker() as session:
        assert await UserRepo(session).get(fake_user.id) is None


async def test_a_failed_user_folder_deletion_keeps_everything(
    client, fake_user, synth, audio_dir, db_session_maker, monkeypatch
):
    def refuse(user_id):
        raise OSError("busy")

    monkeypatch.setattr(store, "delete_user", refuse)
    dream_id = await _dream(client)
    await _rehearse(client, dream_id)

    r = await client.delete("/api/v1/users/me")

    assert r.status_code == 503
    assert r.json() == {"detail": "audio could not be deleted, try again"}
    async with db_session_maker() as session:
        assert await UserRepo(session).get(fake_user.id) is not None
    assert await _row(db_session_maker, dream_id) is not None
    assert _dream_folder(audio_dir, fake_user.id, dream_id).is_dir()


async def test_deleting_an_account_without_audio_works(client, fake_user, audio_dir):
    shutil.rmtree(audio_dir, ignore_errors=True)

    assert (await client.delete("/api/v1/users/me")).status_code == 204


async def test_a_stored_value_that_is_no_key_is_never_linked(
    client, synth, db_session_maker, audio_dir
):
    """A leftover Storage URL in the column: no link, and rehearsal renders anew."""
    dream_id = await _dream(client)
    await _rehearse(client, dream_id)
    async with db_session_maker() as session:
        row = (await session.scalars(select(Dream).where(Dream.id == dream_id))).one()
        row.rehearsal_audio_key = "https://storage.example/storage/v1/object/sign/x.mp3"
        await session.commit()

    r = await client.get("/api/v1/dreams")
    assert r.status_code == 200
    assert r.json()[0]["rehearsal_audio_url"] is None

    dream = await _rehearse(client, dream_id)
    assert _key_of(dream["rehearsal_audio_url"]) in _files(audio_dir)


# --- deletion while a rendering runs --------------------------------------------------


@pytest.mark.parametrize("deletion_first", [True, False])
async def test_an_account_deleted_while_rendering_leaves_no_audio(
    client, fake_user, fake_ai, synth, audio_dir, db_session_maker, monkeypatch, deletion_first
):
    """The account purge runs during the rendering, before or after its write."""
    from redream_api.services import rehearsal_audio

    dream_id = await _dream(client)
    render = fake_ai.rehearsal_generate

    async def racing(payload):
        if deletion_first:
            await rehearsal_audio.delete_user_audio(fake_user.id)
        result = await render(payload)
        if not deletion_first:
            await rehearsal_audio.delete_user_audio(fake_user.id)
        return result

    monkeypatch.setattr(fake_ai, "rehearsal_generate", racing)
    r = await client.post(f"/api/v1/dreams/{dream_id}/rehearsal", json={"language": "en"})

    assert r.status_code == 409
    assert _files(audio_dir) == set()
    row = await _row(db_session_maker, dream_id)
    assert row is not None and row.rehearsal_audio_key is None


async def test_a_dream_edited_while_rendering_keeps_no_stale_audio(
    client, fake_user, fake_ai, synth, audio_dir, db_session_maker, monkeypatch
):
    """The reset of an edit (discard_dream_audio) lands before the write."""
    from redream_api.services import rehearsal_audio

    dream_id = await _dream(client)
    render = fake_ai.rehearsal_generate

    async def racing(payload):
        await rehearsal_audio.discard_dream_audio(fake_user.id, dream_id)
        return await render(payload)

    monkeypatch.setattr(fake_ai, "rehearsal_generate", racing)
    r = await client.post(f"/api/v1/dreams/{dream_id}/rehearsal", json={"language": "en"})

    assert r.status_code == 409
    assert _files(audio_dir) == set()
    row = await _row(db_session_maker, dream_id)
    assert row is not None and row.rehearsal_audio_key is None


async def test_a_failed_rendering_after_a_deletion_removes_its_file(
    client, fake_user, fake_ai, synth, audio_dir, monkeypatch
):
    """The file is written, then the rendering fails (a timeout, say)."""
    from redream_api.services import rehearsal_audio

    dream_id = await _dream(client)
    render = fake_ai.rehearsal_generate

    async def racing(payload):
        await rehearsal_audio.delete_dream_audio(fake_user.id, dream_id)
        await render(payload)
        raise AIServiceError("rehearsal timed out")

    monkeypatch.setattr(fake_ai, "rehearsal_generate", racing)
    r = await client.post(f"/api/v1/dreams/{dream_id}/rehearsal", json={"language": "en"})

    assert r.status_code == 502
    assert _files(audio_dir) == set()


async def test_a_rendering_for_another_dream_is_unaffected(
    client, fake_user, fake_ai, synth, audio_dir, monkeypatch
):
    from redream_api.services import rehearsal_audio

    dream_id = await _dream(client)
    other_id = await _dream(client)
    render = fake_ai.rehearsal_generate

    async def racing(payload):
        await rehearsal_audio.delete_dream_audio(fake_user.id, other_id)
        return await render(payload)

    monkeypatch.setattr(fake_ai, "rehearsal_generate", racing)
    dream = await _rehearse(client, dream_id)

    assert _key_of(dream["rehearsal_audio_url"]) in _files(audio_dir)


async def test_a_dream_row_deleted_after_the_check_is_404(
    client, fake_ai, synth, db_session_maker, monkeypatch
):
    dream_id = await _dream(client)
    render = fake_ai.rehearsal_generate

    async def racing(payload):
        result = await render(payload)
        async with db_session_maker() as session:
            row = (await session.scalars(select(Dream).where(Dream.id == dream_id))).one()
            await session.delete(row)
            await session.commit()
        return result

    monkeypatch.setattr(fake_ai, "rehearsal_generate", racing)
    r = await client.post(f"/api/v1/dreams/{dream_id}/rehearsal", json={"language": "en"})

    assert r.status_code == 404


# --- the daily AI-request cap: renderings only ----------------------------------------


async def test_a_rendering_counts_against_the_daily_cap_and_a_cache_hit_does_not(
    client, fake_user, synth, day
):
    dream_id = await _dream(client)
    for _ in range(99):
        limits.count_ai_request(fake_user.id)

    await _rehearse(client, dream_id)  # the 100th: rendered
    await _rehearse(client, dream_id)  # stored audio: free
    r = await client.post(f"/api/v1/dreams/{dream_id}/rehearsal", json={"language": "de"})

    assert r.status_code == 429
    assert r.json() == {"detail": "daily limit reached"}
    assert r.headers["retry-after"] == str(2 * 3600)  # until UTC midnight
    assert len(synth) == 1


async def test_a_rehearsal_while_speech_output_is_off_is_free(client, fake_user, fake_ai, day):
    dream_id = await _dream(client)
    for _ in range(100):
        limits.count_ai_request(fake_user.id)

    r = await client.post(f"/api/v1/dreams/{dream_id}/rehearsal", json={"language": "en"})

    assert r.status_code == 503
