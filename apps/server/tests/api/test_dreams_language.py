"""dreams.language: set from the chat at the summary (test_chats_language_switch),
optional on manual dreams, never set by other bodies, and the language
rehearsal audio is generated (and cached) in."""

from __future__ import annotations

from typing import Any

import pytest

from redream_api.db.models.dream import Dream

from .conftest import FakeAiClient


async def _create(client, **extra: Any) -> dict[str, Any]:
    r = await client.post(
        "/api/v1/dreams",
        json={
            "title": "Forest",
            "original_dream": "Lost in a forest.",
            "rewritten_dream": "You find the path home.",
            "is_nightmare": True,
            **extra,
        },
    )
    assert r.status_code == 201, r.text
    return dict(r.json())


def _rendered(fake_ai: FakeAiClient) -> list[dict[str, Any]]:
    return [c["payload"] for c in fake_ai.calls if c["method"] == "rehearsal_generate"]


async def test_manual_dream_language_is_optional(client) -> None:
    assert (await _create(client))["language"] is None
    assert (await _create(client, language="de"))["language"] == "de"
    listed = (await client.get("/api/v1/dreams")).json()
    assert sorted(str(d["language"]) for d in listed) == ["None", "de"]


@pytest.mark.parametrize("language", ["fr", "DE", ""])
async def test_manual_dream_language_must_be_en_or_de(client, language) -> None:
    r = await client.post(
        "/api/v1/dreams", json={"title": "x", "original_dream": "y", "language": language}
    )
    assert r.status_code == 422


async def test_update_body_cannot_set_language(client) -> None:
    dream = await _create(client, language="de")
    r = await client.put(f"/api/v1/dreams/{dream['id']}", json={"language": "en"})
    assert r.status_code == 200, r.text
    assert r.json()["language"] == "de"


def test_the_column_is_a_nullable_string() -> None:
    column = Dream.__table__.columns["language"]
    assert column.nullable is True
    assert column.type.python_type is str


async def test_rehearsal_uses_the_dream_language_and_caches_on_it(client, synth, fake_ai) -> None:
    dream = await _create(client, language="de")
    url = f"/api/v1/dreams/{dream['id']}/rehearsal"

    # An older app build sends the app's language; the dream's own wins.
    r = await client.post(url, json={"language": "en"})
    assert r.status_code == 200, r.text
    assert r.json()["rehearsal_language"] == "de"
    (payload,) = _rendered(fake_ai)
    assert payload["language"] == "de"
    first_link = r.json()["rehearsal_audio_url"]
    assert "/de-" in first_link

    # Same body again: served from the audio stored under the dream's language.
    r = await client.post(url, json={"language": "en"})
    assert r.status_code == 200, r.text
    assert r.json()["rehearsal_language"] == "de"
    assert len(_rendered(fake_ai)) == 1
    assert len(synth) == 1
    # And with the dream's language in the body, the same file.
    r = await client.post(url, json={"language": "de"})
    assert len(_rendered(fake_ai)) == 1


async def test_rehearsal_without_dream_language_uses_the_body(client, synth, fake_ai) -> None:
    dream = await _create(client)
    url = f"/api/v1/dreams/{dream['id']}/rehearsal"

    r = await client.post(url, json={"language": "de"})
    assert r.status_code == 200, r.text
    assert r.json()["rehearsal_language"] == "de"
    assert [p["language"] for p in _rendered(fake_ai)] == ["de"]

    # Another body language is a cache miss there: rendered anew in it.
    r = await client.post(url, json={"language": "en"})
    assert r.status_code == 200, r.text
    assert r.json()["rehearsal_language"] == "en"
    assert [p["language"] for p in _rendered(fake_ai)] == ["de", "en"]


async def test_audio_in_another_language_is_rendered_again_in_the_dreams(
    client, synth, fake_ai, db_session_maker
) -> None:
    """Audio stored before the dream had a language (rendered in the app's)
    no longer matches once it does: rendered in the dream's language."""
    from sqlalchemy import select

    dream = await _create(client)
    url = f"/api/v1/dreams/{dream['id']}/rehearsal"
    r = await client.post(url, json={"language": "en"})
    assert r.json()["rehearsal_language"] == "en"

    async with db_session_maker() as session:
        row = (await session.scalars(select(Dream).where(Dream.id == dream["id"]))).one()
        row.language = "de"
        await session.commit()

    r = await client.post(url, json={"language": "en"})
    assert r.status_code == 200, r.text
    assert r.json()["rehearsal_language"] == "de"
    assert [p["language"] for p in _rendered(fake_ai)] == ["en", "de"]


@pytest.fixture
def synth(monkeypatch, fake_ai: FakeAiClient, voice_on) -> list[str]:
    """Speech on, the real AI rehearsal path with only the speech provider
    faked (as in test_rehearsal_audio); returns the scripts rendered."""
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
