"""GDPR purge: the /users/me/data route."""

from __future__ import annotations

from typing import Any


async def test_purge_route_reaches_postgres_and_audio(
    client, fake_user, db_session_maker, monkeypatch
):
    """DELETE /users/me/data must clear the database and the audio folder."""
    from redream_api.api.v1.users import routes as user_routes

    calls: dict[str, Any] = {}

    async def fake_audio(user_id):
        calls["audio"] = user_id

    monkeypatch.setattr(user_routes, "delete_user_audio", fake_audio)

    r = await client.delete("/api/v1/users/me/data")
    assert r.status_code == 204

    assert calls == {"audio": fake_user.id}
    from redream_api.db.repositories.user_repo import UserRepo

    async with db_session_maker() as session:
        assert await UserRepo(session).get(fake_user.id) is None
