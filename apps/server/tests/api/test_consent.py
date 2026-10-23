"""Art. 9 consent: status/grant/withdraw endpoints + the route gate.

The `fake_user` fixture ships WITH consent granted (the normal state for all
feature tests); the no-consent user here exercises the gate itself.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from redream_api.core.config import settings
from redream_api.core.dependencies import get_current_user
from redream_api.db.models.user import User
from redream_api.main import app

from .conftest import NO_PASSWORD


@pytest.fixture
async def no_consent_user(client, db_session_maker) -> User:
    """Swaps the authenticated user for one who never granted consent.

    Reuses the `client` fixture's overrides (db/ai) — only the user changes.
    """
    async with db_session_maker() as session:
        user = User(id=uuid4(), email="fresh@example.com", password_hash=NO_PASSWORD)
        session.add(user)
        await session.commit()
        await session.refresh(user)

    app.dependency_overrides[get_current_user] = lambda: user
    return user


async def test_consent_lifecycle(client, no_consent_user):
    # Fresh account: consent required.
    r = await client.get("/api/v1/users/me/consent")
    assert r.status_code == 200
    body = r.json()
    assert body["consent_required"] is True
    assert body["granted_version"] is None
    assert body["required_version"] == settings.consent_version

    # Grant.
    r = await client.post(
        "/api/v1/users/me/consent",
        json={"version": settings.consent_version, "text_version": settings.consent_version},
    )
    assert r.status_code == 204
    body = (await client.get("/api/v1/users/me/consent")).json()
    assert body["consent_required"] is False
    assert body["granted_version"] == settings.consent_version

    # Withdraw — as easy as granting.
    r = await client.delete("/api/v1/users/me/consent")
    assert r.status_code == 204
    body = (await client.get("/api/v1/users/me/consent")).json()
    assert body["consent_required"] is True
    assert body["granted_version"] is None

    # Re-grant works (append-only log, latest row wins).
    r = await client.post(
        "/api/v1/users/me/consent",
        json={"version": settings.consent_version, "text_version": settings.consent_version},
    )
    assert r.status_code == 204
    assert (await client.get("/api/v1/users/me/consent")).json()["consent_required"] is False


async def test_stale_consent_version_is_rejected(client, no_consent_user):
    """A grant recorded off an outdated consent screen must not count."""
    r = await client.post(
        "/api/v1/users/me/consent",
        json={"version": "2020-01-01", "text_version": "2020-01-01"},
    )
    assert r.status_code == 409


async def test_version_bump_forces_reconsent(client, fake_user, monkeypatch):
    """fake_user granted the current version; a copy change re-gates them."""
    monkeypatch.setattr(settings, "consent_version", "2099-01-01")

    body = (await client.get("/api/v1/users/me/consent")).json()
    assert body["consent_required"] is True

    r = await client.post("/api/v1/chats/start", json={"language": "en"})
    assert r.status_code == 403
    assert r.json()["detail"] == "consent_required"


async def test_gate_blocks_health_data_routes_without_consent(client, no_consent_user):
    gated = [
        ("post", "/api/v1/chats/start", {"json": {"language": "en"}}),
        ("post", "/api/v1/chats/1/message", {"json": {"content": "hi"}}),
        ("post", "/api/v1/chats/1/message/stream", {"json": {"content": "hi"}}),
        ("put", "/api/v1/chats/1/intensity", {"json": {"intensity": 5}}),
        ("post", "/api/v1/dreams", {"json": {"title": "t", "original_dream": "d"}}),
        (
            "post",
            "/api/v1/audio/transcribe",
            {"files": {"file": ("a.wav", b"x", "audio/wav")}},
        ),
    ]
    for method, url, kwargs in gated:
        r = await getattr(client, method)(url, **kwargs)
        assert r.status_code == 403, f"{method} {url} → {r.status_code}"
        assert r.json()["detail"] == "consent_required", url


async def test_reads_and_deletion_stay_open_without_consent(client, no_consent_user):
    """Withdrawal must never lock users out of their own data (Art. 7(3))."""
    assert (await client.get("/api/v1/chats")).status_code == 200
    assert (await client.get("/api/v1/dreams")).status_code == 200
    # Account deletion works regardless of consent state.
    r = await client.delete("/api/v1/users/me")
    assert r.status_code == 204


async def test_old_clients_cannot_echo_new_server_version_without_new_copy(client, no_consent_user):
    """Builds without text_version reload the form silently on a 409, so
    they get a 426 whose text they show: install the new version."""
    r = await client.post("/api/v1/users/me/consent", json={"version": settings.consent_version})
    assert r.status_code == 426
    assert "latest version of the app" in r.json()["detail"]
    assert "neueste Version der App" in r.json()["detail"]
    status = await client.get("/api/v1/users/me/consent")
    assert status.json()["consent_required"] is True
