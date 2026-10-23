"""Account deletion removes the Supabase login, not only the app data.

`auth_service.delete_login` calls the Auth admin API over httpx; tests install
an `httpx.MockTransport` via the module's `_transport` seam.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import httpx
import pytest

from redream_api.db.repositories.user_repo import UserRepo
from redream_api.services import auth_service


@pytest.fixture
def auth_settings(monkeypatch):
    monkeypatch.setattr(auth_service.settings, "supabase_url", "http://kong:8000/")
    monkeypatch.setattr(auth_service.settings, "supabase_service_role_key", "srk")


def _auth_api(monkeypatch, handler) -> list[httpx.Request]:
    requests: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    monkeypatch.setattr(auth_service, "_transport", httpx.MockTransport(record))
    return requests


async def test_delete_login_calls_the_admin_api(auth_settings, monkeypatch):
    requests = _auth_api(monkeypatch, lambda _: httpx.Response(200, json={}))
    user_id = uuid4()

    assert await auth_service.delete_login(user_id) is True

    (request,) = requests
    assert request.method == "DELETE"
    assert str(request.url) == f"http://kong:8000/auth/v1/admin/users/{user_id}"
    assert request.headers["apikey"] == "srk"
    assert request.headers["authorization"] == "Bearer srk"


async def test_a_login_that_is_already_gone_counts_as_deleted(auth_settings, monkeypatch):
    _auth_api(monkeypatch, lambda _: httpx.Response(404, json={"error_code": "user_not_found"}))

    assert await auth_service.delete_login(uuid4()) is True


async def test_delete_login_reports_a_refusal(auth_settings, monkeypatch):
    _auth_api(monkeypatch, lambda _: httpx.Response(500, text="boom"))

    assert await auth_service.delete_login(uuid4()) is False


async def test_delete_login_reports_an_unreachable_auth_service(auth_settings, monkeypatch):
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    _auth_api(monkeypatch, unreachable)

    assert await auth_service.delete_login(uuid4()) is False


async def test_delete_login_fails_without_supabase_settings(monkeypatch):
    monkeypatch.setattr(auth_service.settings, "supabase_url", "")
    requests = _auth_api(monkeypatch, lambda _: httpx.Response(200, json={}))

    assert await auth_service.delete_login(uuid4()) is False
    assert requests == []


def _fake_purges(monkeypatch, *, login_deleted: bool) -> list[str]:
    from redream_api.api.v1.users import routes as user_routes

    order: list[str] = []

    def step(name: str, result: Any):
        async def run(user_id):
            order.append(name)
            return result

        return run

    monkeypatch.setattr(user_routes, "delete_user_audio", step("audio", None))
    monkeypatch.setattr(user_routes, "delete_login", step("login", login_deleted))
    return order


async def test_account_deletion_removes_the_login_last(
    client, fake_user, db_session_maker, monkeypatch
):
    order = _fake_purges(monkeypatch, login_deleted=True)

    r = await client.delete("/api/v1/users/me")

    assert r.status_code == 204
    assert order == ["audio", "login"]
    async with db_session_maker() as session:
        assert await UserRepo(session).get(fake_user.id) is None


async def test_account_deletion_fails_when_the_login_stays(
    client, fake_user, db_session_maker, monkeypatch
):
    _fake_purges(monkeypatch, login_deleted=False)

    r = await client.delete("/api/v1/users/me")

    # The app then asks the user to try again; their data is gone either way.
    assert r.status_code == 503
    async with db_session_maker() as session:
        assert await UserRepo(session).get(fake_user.id) is None


async def test_the_data_purge_keeps_the_login(client, monkeypatch):
    order = _fake_purges(monkeypatch, login_deleted=True)

    r = await client.delete("/api/v1/users/me/data")

    assert r.status_code == 204
    assert order == ["audio"]
