"""Account deletion: DELETE /users/me removes the user's audio folder, then
the `users` row; the database cascades to the login tokens and every row of
the user. The data-only purge (/users/me/data) is gone: with the server's own
login it would have deleted the login too.

Runs with real tokens (`auth_client`), so the login's end is observable.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from redream_ai.audio import store
from redream_api.core.config import settings
from redream_api.db.base import Base
from redream_api.db.models.auth_token import AuthToken
from redream_api.db.models.chat import Chat
from redream_api.db.models.dream import Dream
from redream_api.db.models.user import User
from redream_api.db.models.user_consent import UserConsent
from redream_api.db.repositories.auth_token_repo import AuthTokenRepo
from redream_api.db.session import make_engine

PASSWORD = "correct horse battery"


async def _account(client, email: str) -> tuple[UUID, dict[str, str]]:
    """A registered, consenting user with a dream and a chat."""
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "invite_code": settings.invite_code},
    )
    assert r.status_code == 201, r.text
    user_id = UUID(r.json()["user"]["id"])
    headers = {"Authorization": f"Bearer {r.json()['token']}"}
    version = settings.consent_version
    r = await client.post(
        "/api/v1/users/me/consent",
        json={"version": version, "text_version": version},
        headers=headers,
    )
    assert r.status_code == 204, r.text
    r = await client.post(
        "/api/v1/dreams",
        json={"title": "Forest", "original_dream": "Lost.", "is_nightmare": True},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    assert (await client.post("/api/v1/chats/start", json={}, headers=headers)).status_code == 201
    return user_id, headers


async def _rows(db_session_maker, user_id: UUID) -> dict[str, int]:
    async with db_session_maker() as session:
        return {
            model.__name__: await session.scalar(
                select(func.count()).select_from(model).where(column == user_id)
            )
            for model, column in (
                (User, User.id),
                (AuthToken, AuthToken.user_id),
                (Dream, Dream.user_id),
                (Chat, Chat.user_id),
                (UserConsent, UserConsent.user_id),
            )
        }


async def test_account_deletion_removes_login_rows_and_audio(
    auth_client, db_session_maker, audio_dir
):
    alice, headers = await _account(auth_client, "alice@example.com")
    bob, bob_headers = await _account(auth_client, "bob@example.com")
    r = await auth_client.post(
        "/api/v1/auth/login", json={"email": "alice@example.com", "password": PASSWORD}
    )
    second = {"Authorization": f"Bearer {r.json()['token']}"}
    store.write(store.key_for(alice, 1, "en", "a" * 24), b"ID3 alice")
    store.write(store.key_for(bob, 2, "en", "b" * 24), b"ID3 bob")
    assert (await _rows(db_session_maker, alice))["AuthToken"] == 2

    r = await auth_client.delete("/api/v1/users/me", headers=headers)

    assert r.status_code == 204
    assert set((await _rows(db_session_maker, alice)).values()) == {0}
    assert not (audio_dir / "users" / str(alice)).exists()
    for gone in (headers, second):
        r = await auth_client.post("/api/v1/auth/session/validate", headers=gone)
        assert r.status_code == 401
    r = await auth_client.post(
        "/api/v1/auth/login", json={"email": "alice@example.com", "password": PASSWORD}
    )
    assert r.status_code == 401
    # Nobody else's account is touched.
    assert set((await _rows(db_session_maker, bob)).values()) == {1}
    assert (audio_dir / "users" / str(bob)).is_dir()
    r = await auth_client.post("/api/v1/auth/session/validate", headers=bob_headers)
    assert r.status_code == 204


async def test_a_failed_audio_deletion_keeps_the_account(
    auth_client, db_session_maker, monkeypatch
):
    alice, headers = await _account(auth_client, "alice@example.com")

    def refuse(user_id):
        raise OSError("busy")

    monkeypatch.setattr(store, "delete_user", refuse)

    r = await auth_client.delete("/api/v1/users/me", headers=headers)

    assert r.status_code == 503
    assert set((await _rows(db_session_maker, alice)).values()) == {1}
    r = await auth_client.post("/api/v1/auth/session/validate", headers=headers)
    assert r.status_code == 204


async def test_the_app_engine_cascades_on_sqlite_too(tmp_path: Path):
    """DATABASE_URL may name SQLite: the app's engine (and the CLI's) then
    enforces the foreign keys the deletion relies on, as Postgres does."""
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'app.db'}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(bind=engine, class_=AsyncSession)() as session:
            user_id = uuid4()
            session.add(User(id=user_id, email="alice@example.com", password_hash="x"))
            await session.commit()
            await AuthTokenRepo(session).issue(user_id)

            # The database's cascade, not the ORM's.
            await session.execute(delete(User).where(User.id == user_id))
            await session.commit()

            assert await session.scalar(select(func.count()).select_from(AuthToken)) == 0
    finally:
        await engine.dispose()


async def test_deletion_needs_a_token(auth_client):
    assert (await auth_client.delete("/api/v1/users/me")).status_code == 401


async def test_the_data_only_purge_is_gone(client):
    r = await client.delete("/api/v1/users/me/data")

    assert r.status_code in (404, 405)
