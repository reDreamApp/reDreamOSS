"""GDPR purge: the /users/me/data route."""

from __future__ import annotations

from typing import Any


async def test_purge_route_reaches_postgres_and_storage(
    client, fake_user, db_session_maker, monkeypatch
):
    """DELETE /users/me/data must clear both stores (apps/api invariant)."""
    from redream_api.api.v1.users import routes as user_routes

    calls: dict[str, Any] = {}

    async def fake_storage(user_id):
        calls["storage"] = user_id

    monkeypatch.setattr(user_routes, "delete_rehearsal_audio_for_user", fake_storage)

    r = await client.delete("/api/v1/users/me/data")
    assert r.status_code == 204

    assert calls == {"storage": fake_user.id}
    from redream_api.db.repositories.user_repo import UserRepo

    async with db_session_maker() as session:
        assert await UserRepo(session).get(fake_user.id) is None
