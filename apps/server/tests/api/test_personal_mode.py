"""Personal mode: one local user, signed in from this machine only; a SQLite
file database; the web app served by the server itself."""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from redream_api import main
from redream_api.core.config import settings
from redream_api.core.dependencies import get_db
from redream_api.db import migrate
from redream_api.main import app
from redream_api.services.ai_client import get_ai_client

KEY = "k" * 43
WITH_KEY = {"X-Personal-Key": KEY}


@pytest.fixture
def personal(monkeypatch) -> None:
    monkeypatch.setattr(settings, "registration_mode", "personal")
    monkeypatch.setattr(settings, "personal_key", KEY)


@pytest.fixture
async def local(db_session_maker, fake_ai) -> AsyncIterator[AsyncClient]:
    """A client as a browser on this machine sees the server (http://localhost:8000)."""

    async def _db_dep() -> AsyncIterator[AsyncSession]:
        async with db_session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = _db_dep
    app.dependency_overrides[get_ai_client] = lambda: fake_ai
    transport = ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with AsyncClient(transport=transport, base_url="http://localhost:8000") as c:
        yield c
    app.dependency_overrides.clear()


async def test_the_local_browser_gets_a_session_for_the_one_user(personal, local) -> None:
    first = await local.post("/api/v1/auth/personal", headers=WITH_KEY)
    second = await local.post("/api/v1/auth/personal", headers=WITH_KEY)

    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["user"] == second.json()["user"]
    assert first.json()["token"] != second.json()["token"]
    token = first.json()["token"]
    r = await local.post(
        "/api/v1/auth/session/validate", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 204


@pytest.mark.parametrize("host", ["127.0.0.1:8000", "[::1]:8000", "localhost"])
async def test_every_local_host_name_works(personal, local, host) -> None:
    r = await local.post("/api/v1/auth/personal", headers={"Host": host, **WITH_KEY})
    assert r.status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "dreams.example.org"},  # a tunnel or a DNS-rebinding page
        {"X-Forwarded-For": "203.0.113.7"},  # a reverse proxy on this machine
        {"Forwarded": "for=203.0.113.7"},
        {"CF-Connecting-IP": "203.0.113.7"},
    ],
)
async def test_proxied_or_foreign_requests_are_refused(personal, local, headers) -> None:
    r = await local.post("/api/v1/auth/personal", headers={**headers, **WITH_KEY})
    assert r.status_code == 403


async def test_another_machine_is_refused(personal, db_session_maker, fake_ai) -> None:
    async def _db_dep() -> AsyncIterator[AsyncSession]:
        async with db_session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = _db_dep
    transport = ASGITransport(app=app, client=("192.168.1.20", 50000))
    try:
        async with AsyncClient(transport=transport, base_url="http://localhost:8000") as c:
            r = await c.post("/api/v1/auth/personal", headers=WITH_KEY)
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 403


@pytest.mark.parametrize(
    ("headers", "detail"),
    [
        ({}, "personal key required"),
        ({"X-Personal-Key": "k" * 42 + "x"}, "wrong personal key"),
    ],
)
async def test_the_personal_key_is_required(personal, local, headers, detail) -> None:
    """A forwarder that adds no headers (ssh -R, socat) or another account on
    this machine passes the local checks; only the key holder signs in."""
    r = await local.post("/api/v1/auth/personal", headers=headers)
    assert r.status_code == 403
    assert r.json() == {"detail": detail}


def test_personal_mode_needs_a_key(monkeypatch) -> None:
    from pydantic import ValidationError

    from redream_api.core.config import Settings

    monkeypatch.setenv("REGISTRATION_MODE", "personal")
    monkeypatch.setenv("PERSONAL_KEY", "short")
    with pytest.raises(ValidationError, match="PERSONAL_KEY must be at least 32"):
        Settings(_env_file=None)


async def test_no_personal_sign_in_on_a_shared_server(local) -> None:
    assert settings.registration_mode == "invite"
    assert (await local.post("/api/v1/auth/personal")).status_code == 404


async def test_personal_servers_take_no_sign_ups(personal, local) -> None:
    r = await local.post(
        "/api/v1/auth/register",
        json={"email": "a@example.org", "password": "a-password-1", "invite_code": None},
    )
    assert r.status_code == 403


async def test_the_instance_says_personal(personal, local) -> None:
    assert (await local.get("/api/v1/instance")).json()["registration"] == "personal"


# --- the SQLite database ------------------------------------------------------------


async def test_a_new_sqlite_file_gets_the_schema_and_the_newest_version(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'redream.sqlite3'}"
    migrations = migrate.load()

    assert await migrate.run_sqlite(url, migrations) == "created at 0001"
    assert await migrate.run_sqlite(url, migrations) == "at 0001"
    with sqlite3.connect(tmp_path / "redream.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone() == (1,)
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"users", "auth_tokens", "chats", "chat_messages", "dreams"} <= tables


async def test_an_older_sqlite_file_is_upgraded_step_by_step(tmp_path: Path, monkeypatch) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'redream.sqlite3'}"
    first = migrate.load()
    await migrate.run_sqlite(url, first)
    later = [*first, ("0002_notes.sql", "ALTER TABLE dreams ADD COLUMN notes TEXT", "x")]

    with pytest.raises(migrate.MigrationError, match="no SQLite step for migration 0002"):
        await migrate.run_sqlite(url, later)
    monkeypatch.setitem(migrate.SQLITE_STEPS, 2, ("ALTER TABLE dreams ADD COLUMN notes TEXT",))
    assert await migrate.run_sqlite(url, later) == "upgraded to 0002"
    with sqlite3.connect(tmp_path / "redream.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone() == (2,)
        assert "notes" in {row[1] for row in db.execute("PRAGMA table_info(dreams)")}


async def test_a_failing_sqlite_step_leaves_the_file_as_it_was(tmp_path: Path, monkeypatch) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'redream.sqlite3'}"
    first = migrate.load()
    await migrate.run_sqlite(url, first)
    monkeypatch.setitem(
        migrate.SQLITE_STEPS,
        2,
        ("ALTER TABLE dreams ADD COLUMN notes TEXT", "ALTER TABLE no_such_table ADD COLUMN x TEXT"),
    )

    with pytest.raises(SQLAlchemyError):
        await migrate.run_sqlite(url, [*first, ("0002_notes.sql", "", "x")])

    with sqlite3.connect(tmp_path / "redream.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone() == (1,)
        assert "notes" not in {row[1] for row in db.execute("PRAGMA table_info(dreams)")}


def test_every_migration_after_the_first_has_a_sqlite_step() -> None:
    """A schema change ships for both databases: personal files are upgraded
    by SQLITE_STEPS, shared servers by the SQL file."""
    numbers = [int(name.split("_", 1)[0]) for name, _, _ in migrate.load()]
    assert [n for n in numbers if n > 1 and n not in migrate.SQLITE_STEPS] == []


# --- the web app --------------------------------------------------------------------


async def test_the_web_app_is_served_next_to_the_api(tmp_path: Path, local) -> None:
    (tmp_path / "index.html").write_text("<!doctype html><title>reDreamOSS</title>")
    (tmp_path / "main.dart.js").write_text("// app")
    main._mount_web_app(str(tmp_path))
    try:
        page = await local.get("/")
        script = await local.get("/main.dart.js")
        api = await local.get("/api/v1/health")
        unknown_api = await local.get("/api/v1/no-such-route")
    finally:
        app.router.routes[:] = [r for r in app.router.routes if getattr(r, "name", "") != "web"]

    assert page.status_code == 200 and "reDreamOSS" in page.text
    assert page.headers["x-frame-options"] == "DENY"
    assert page.headers["content-security-policy"] == "frame-ancestors 'none'"
    assert script.status_code == 200
    assert api.json()["status"] == "ok"
    assert unknown_api.status_code == 404 and unknown_api.json() == {"detail": "Not Found"}


def test_a_web_dir_without_an_index_is_not_served(tmp_path: Path) -> None:
    before = len(app.router.routes)
    main._mount_web_app(str(tmp_path))
    assert len(app.router.routes) == before


async def test_timestamps_the_database_sets_keep_their_zone(client) -> None:
    """SQLite fills created_at without a zone; the API must still say UTC, or
    the app shows UTC as local time (seen in the S22 browser run)."""
    from .conftest import start_chat

    chat_id = await start_chat(client)
    dream = (
        await client.post("/api/v1/dreams", json={"title": "Storm", "original_dream": "A storm."})
    ).json()
    chats = (await client.get("/api/v1/chats")).json()

    chat = next(c for c in chats if c["id"] == chat_id)
    for stamp in (dream["created_at"], chat["created_at"], chat["updated_at"]):
        assert stamp.endswith(("Z", "+00:00")), stamp
