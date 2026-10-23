"""The real app on the migrated schema: an account's life from registration to
deletion, through every table, with the database's cascades doing the purge."""

from __future__ import annotations

from collections.abc import AsyncIterator

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from redream_api.core import consent
from redream_api.core.config import settings
from redream_api.core.dependencies import get_db
from redream_api.db import migrate
from redream_api.db.base import Base
from redream_api.db.models.chat_message import ChatMessage
from redream_api.db.session import make_engine
from redream_api.main import app

PASSWORD = "correct horse battery"


async def _counts(session_maker: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    async with session_maker() as session:
        return {
            name: int(await session.scalar(select(func.count()).select_from(table)) or 0)
            for name, table in Base.metadata.tables.items()
        }


async def test_an_account_from_registration_to_deletion(empty_db, monkeypatch):
    await migrate.run(empty_db, migrate.load())
    engine = make_engine(empty_db)
    session_maker = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

    async def _db_dep() -> AsyncIterator[AsyncSession]:
        async with session_maker() as session:
            yield session

    monkeypatch.setattr(settings, "registration_mode", "open")
    app.dependency_overrides[get_db] = _db_dep
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/v1/auth/register", json={"email": "Alice@Example.com", "password": PASSWORD}
            )
            assert r.status_code == 201, r.text
            # The same address in another case is taken.
            r = await c.post(
                "/api/v1/auth/register", json={"email": "ALICE@example.com", "password": PASSWORD}
            )
            assert r.status_code == 409, r.text

            r = await c.post(
                "/api/v1/auth/login", json={"email": "alice@example.com", "password": PASSWORD}
            )
            assert r.status_code == 200, r.text
            headers = {"Authorization": f"Bearer {r.json()['token']}"}

            version = consent.CONSENT_VERSION
            r = await c.post(
                "/api/v1/users/me/consent",
                json={"version": version, "text_version": version},
                headers=headers,
            )
            assert r.status_code == 204, r.text
            r = await c.post(
                "/api/v1/dreams",
                json={"title": "Forest", "original_dream": "Lost.", "language": "de"},
                headers=headers,
            )
            assert r.status_code == 201, r.text
            assert r.json()["practice_timestamps"] == []
            # A manual dream without a language passes dreams_language_check.
            r = await c.post(
                "/api/v1/dreams", json={"title": "Sea", "original_dream": "Waves."}, headers=headers
            )
            assert r.status_code == 201, r.text
            assert r.json()["language"] is None
            # Out of the CHECK's range: a 422 from the API, not a database error.
            r = await c.post(
                "/api/v1/dreams",
                json={"title": "Sea", "original_dream": "Waves.", "intensity": 2**31},
                headers=headers,
            )
            assert r.status_code == 422, r.text
            r = await c.post("/api/v1/chats/start", json={"language": "de"}, headers=headers)
            assert r.status_code == 201, r.text
            chat_id = r.json()["chat_id"]
            r = await c.post("/api/v1/progress/emotion", json={"emotion": "calm"}, headers=headers)
            assert r.status_code == 204, r.text
            # A turn, written directly: chat_messages hang off chats, not users.
            async with session_maker() as session:
                session.add(ChatMessage(chat_id=chat_id, role="user", content="Hi"))
                await session.commit()

            counts = await _counts(session_maker)
            assert all(counts.values()), counts  # every table has a row
            assert counts["users"] == 1

            r = await c.delete("/api/v1/users/me", headers=headers)
            assert r.status_code == 204, r.text
            r = await c.get("/api/v1/dreams", headers=headers)
            assert r.status_code == 401, r.text

        # The only account is gone, and every row with it.
        assert await _counts(session_maker) == dict.fromkeys(Base.metadata.tables, 0)
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
