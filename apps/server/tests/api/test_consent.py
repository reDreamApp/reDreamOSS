"""Art. 9 consent: status/grant/withdraw endpoints + the route gate.

The `fake_user` fixture ships WITH consent granted (the normal state for all
feature tests); the no-consent user here exercises the gate itself.
"""

from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from redream_api.core import consent
from redream_api.core.config import Settings
from redream_api.core.dependencies import get_current_user
from redream_api.db.models.user import User
from redream_api.db.models.user_consent import UserConsent
from redream_api.main import app

from .conftest import NO_PASSWORD

# The app's bundled consent copy version (S12: one constant on each side).
_APP_CONSENT_SERVICE = (
    Path(__file__).resolve().parents[3] / "app/lib/features/consent/consent_service.dart"
)


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
    assert body["required_version"] == consent.CONSENT_VERSION

    # Grant.
    r = await client.post(
        "/api/v1/users/me/consent",
        json={"version": consent.CONSENT_VERSION, "text_version": consent.CONSENT_VERSION},
    )
    assert r.status_code == 204
    body = (await client.get("/api/v1/users/me/consent")).json()
    assert body["consent_required"] is False
    assert body["granted_version"] == consent.CONSENT_VERSION

    # Withdraw — as easy as granting.
    r = await client.delete("/api/v1/users/me/consent")
    assert r.status_code == 204
    body = (await client.get("/api/v1/users/me/consent")).json()
    assert body["consent_required"] is True
    assert body["granted_version"] is None

    # Re-grant works (append-only log, latest row wins).
    r = await client.post(
        "/api/v1/users/me/consent",
        json={"version": consent.CONSENT_VERSION, "text_version": consent.CONSENT_VERSION},
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
    monkeypatch.setattr(consent, "CONSENT_VERSION", "2099-01-01")

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
    r = await client.post("/api/v1/users/me/consent", json={"version": consent.CONSENT_VERSION})
    assert r.status_code == 426
    assert "latest version of the app" in r.json()["detail"]
    assert "neueste Version der App" in r.json()["detail"]
    status = await client.get("/api/v1/users/me/consent")
    assert status.json()["consent_required"] is True


# --- the version constant (core/consent) ----------------------------------------


def test_server_version_equals_the_apps():
    """A mismatch would refuse every grant from that app build (409)."""
    match = re.search(
        r"^const consentTextVersion = '([^']+)';$",
        _APP_CONSENT_SERVICE.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert match, f"consentTextVersion not found in {_APP_CONSENT_SERVICE}"
    assert consent.CONSENT_VERSION == match.group(1)


def test_version_is_not_a_setting(monkeypatch):
    """CONSENT_VERSION in the environment no longer changes anything."""
    monkeypatch.setenv("CONSENT_VERSION", "2099-01-01")
    s = Settings(_env_file=None)
    assert not hasattr(s, "consent_version")
    assert "consent_version" not in Settings.model_fields


async def test_gate_follows_the_module_attribute_at_call_time(
    client, no_consent_user, db_session_maker, monkeypatch
):
    """Status, grant, withdraw and the gate all read consent.CONSENT_VERSION
    per request: a grant of the patched version opens the gate, putting the
    old version back closes it again, and a withdrawal records the patched one."""
    original = consent.CONSENT_VERSION
    monkeypatch.setattr(consent, "CONSENT_VERSION", "2099-01-01")

    status = (await client.get("/api/v1/users/me/consent")).json()
    assert status["required_version"] == "2099-01-01"
    r = await client.post(
        "/api/v1/users/me/consent", json={"version": original, "text_version": original}
    )
    assert r.status_code == 409  # the old screen no longer counts
    r = await client.post(
        "/api/v1/users/me/consent", json={"version": "2099-01-01", "text_version": "2099-01-01"}
    )
    assert r.status_code == 204
    assert (await client.post("/api/v1/chats/start", json={"language": "en"})).status_code == 201

    monkeypatch.setattr(consent, "CONSENT_VERSION", original)
    r = await client.post("/api/v1/chats/start", json={"language": "en"})
    assert r.status_code == 403
    assert r.json()["detail"] == "consent_required"

    # The withdrawal is logged under the version in force at that moment.
    monkeypatch.setattr(consent, "CONSENT_VERSION", "2099-01-01")
    assert (await client.delete("/api/v1/users/me/consent")).status_code == 204
    async with db_session_maker() as session:
        latest = await session.scalar(
            select(UserConsent)
            .where(UserConsent.user_id == no_consent_user.id)
            .order_by(UserConsent.id.desc())
            .limit(1)
        )
    assert latest is not None
    assert (latest.version, latest.granted) == ("2099-01-01", False)
