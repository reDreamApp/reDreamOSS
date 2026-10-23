"""Own auth: register, login, bearer tokens, logout, password and email change,
the rate limits in front of them, and the settings that choose who may register.

Everything here runs through `auth_client`: the real get_current_user, with
tokens from the routes themselves.
"""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from argon2 import PasswordHasher
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from redream_api import main
from redream_api.core import limits, passwords
from redream_api.core.config import Settings, settings
from redream_api.db import session as db_session
from redream_api.db.models.auth_token import AuthToken
from redream_api.db.models.user import User
from redream_api.db.repositories import auth_token_repo
from redream_api.db.repositories.user_repo import UserRepo
from redream_api.main import app

PASSWORD = "correct horse battery"
INVITE = "test-invite-code"  # tests/api/conftest.py


class CountingHasher(PasswordHasher):
    """argon2id with cheap parameters, counting every hash and verification."""

    def __init__(self, **params: int) -> None:
        super().__init__(**({"time_cost": 1, "memory_cost": 1024, "parallelism": 1} | params))
        self.calls = 0

    def hash(self, password, *, salt=None):
        self.calls += 1
        return super().hash(password, salt=salt)

    def verify(self, hash, password):
        self.calls += 1
        return super().verify(hash, password)


@pytest.fixture
def hasher(monkeypatch) -> CountingHasher:
    """Cheap, counted argon2 for this test (the dummy hash is made anew with it).
    The semaphore too: one that waited once is bound to that test's event loop."""
    counting = CountingHasher()
    monkeypatch.setattr(passwords, "_hasher", counting)
    monkeypatch.setattr(passwords, "_dummy_hash", None)
    monkeypatch.setattr(passwords, "_slots", asyncio.Semaphore(passwords._CONCURRENCY))
    return counting


@pytest.fixture
def clock(monkeypatch):
    """The token clock, movable: clock.now += timedelta(...)."""

    class Clock:
        now = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)

    monkeypatch.setattr(auth_token_repo, "_utcnow", lambda: Clock.now)
    return Clock


async def register(client, email="alice@example.com", password=PASSWORD, invite_code=INVITE):
    body = {"email": email, "password": password}
    if invite_code is not None:
        body["invite_code"] = invite_code
    return await client.post("/api/v1/auth/register", json=body)


async def login(client, email="alice@example.com", password=PASSWORD):
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


async def signed_up(client, email="alice@example.com") -> str:
    r = await register(client, email)
    assert r.status_code == 201, r.text
    return str(r.json()["token"])


def client_from(host: str) -> AsyncClient:
    """A client on the same app (and overrides) from another peer address."""
    transport = ASGITransport(app=app, client=(host, 4711))
    return AsyncClient(transport=transport, base_url="http://test")


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def validate(client, token: str) -> int:
    r = await client.post("/api/v1/auth/session/validate", headers=bearer(token))
    return r.status_code


async def token_rows(db_session_maker) -> list[AuthToken]:
    async with db_session_maker() as session:
        return list((await session.scalars(select(AuthToken))).all())


# --- registration -------------------------------------------------------------------


async def test_register_with_the_invite_code(auth_client, db_session_maker):
    r = await register(auth_client, email="  Alice@Example.COM ")

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["user"]["email"] == "alice@example.com"
    assert body["token"].startswith("rdo_")
    assert await validate(auth_client, body["token"]) == 204
    async with db_session_maker() as session:
        user = (await session.scalars(select(User))).one()
    assert str(user.id) == body["user"]["id"]
    assert user.password_hash.startswith("$argon2id$")
    assert PASSWORD not in user.password_hash


@pytest.mark.parametrize("code", ["wrong-invite-code", None, ""])
async def test_register_without_the_right_invite_code_is_403(auth_client, code):
    r = await register(auth_client, invite_code=code)

    assert r.status_code == 403
    assert r.json() == {"detail": "invalid invite code"}


async def test_open_registration_needs_no_code(auth_client, monkeypatch):
    monkeypatch.setattr(settings, "registration_mode", "open")

    assert (await register(auth_client, invite_code=None)).status_code == 201


async def test_closed_registration_is_403(auth_client, monkeypatch):
    monkeypatch.setattr(settings, "registration_mode", "closed")

    r = await register(auth_client)

    assert r.status_code == 403
    assert r.json() == {"detail": "registration closed"}


async def test_an_email_registers_once_whatever_its_case(auth_client):
    await signed_up(auth_client)

    r = await register(auth_client, email="ALICE@example.com")

    assert r.status_code == 409
    assert r.json() == {"detail": "email already registered"}


@pytest.mark.parametrize(
    "email", ["not-an-email", "a@b", "", "a" * 250 + "@example.com", "two@at@example.com"]
)
async def test_register_refuses_an_invalid_email(auth_client, email):
    assert (await register(auth_client, email=email)).status_code == 422


@pytest.mark.parametrize("password", ["seven77", "x" * 1025])
async def test_register_refuses_a_short_or_overlong_password_before_hashing(
    auth_client, hasher, password
):
    assert (await register(auth_client, password=password)).status_code == 422
    assert hasher.calls == 0


async def test_passwords_of_8_and_1024_characters_are_fine(auth_client, hasher):
    assert (await register(auth_client, "a@example.com", "x" * 8)).status_code == 201
    assert (await register(auth_client, "b@example.com", "ü" * 1024)).status_code == 201


# --- settings -----------------------------------------------------------------------


@pytest.mark.parametrize("code", ["", "   ", "seven77", "  seven77  "])
def test_invite_mode_refuses_a_short_invite_code(monkeypatch, code):
    monkeypatch.setenv("REGISTRATION_MODE", "invite")
    monkeypatch.setenv("INVITE_CODE", code)

    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None)

    message = str(exc.value)
    assert "INVITE_CODE" in message
    assert "REGISTRATION_MODE=open" in message and "REGISTRATION_MODE=closed" in message
    if code.strip():
        assert code.strip() not in message


def test_invite_mode_refuses_an_invite_code_no_one_could_enter(monkeypatch):
    monkeypatch.setenv("REGISTRATION_MODE", "invite")
    monkeypatch.setenv("INVITE_CODE", "x" * 257)

    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None)

    assert "INVITE_CODE must be at most 256 characters" in str(exc.value)
    monkeypatch.setenv("INVITE_CODE", "x" * 256)
    assert len(Settings(_env_file=None).invite_code) == 256


@pytest.mark.parametrize("mode", ["open", "closed", " Closed "])
def test_open_and_closed_need_no_invite_code(monkeypatch, mode):
    monkeypatch.setenv("REGISTRATION_MODE", mode)
    monkeypatch.setenv("INVITE_CODE", "")

    assert Settings(_env_file=None).registration_mode == mode.strip().lower()


def test_account_settings_defaults(monkeypatch):
    monkeypatch.delenv("REGISTRATION_MODE")
    monkeypatch.delenv("DAILY_AI_REQUESTS_PER_USER")
    monkeypatch.setenv("INVITE_CODE", "  eight888  ")

    s = Settings(_env_file=None)

    assert (s.registration_mode, s.invite_code, s.daily_ai_requests_per_user) == (
        "invite",
        "eight888",
        100,
    )
    monkeypatch.setenv("DAILY_AI_REQUESTS_PER_USER", "-1")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_the_supabase_settings_are_gone():
    from redream_ai.config import Settings as AiSettings

    for fields in (Settings.model_fields, AiSettings.model_fields):
        assert not [name for name in fields if "supabase" in name or "jwt" in name]


# --- login --------------------------------------------------------------------------


async def test_login_returns_a_new_token(auth_client, clock):
    first = await signed_up(auth_client)

    r = await login(auth_client, email=" ALICE@example.com")

    assert r.status_code == 200
    body = r.json()
    assert body["token"] != first
    assert body["user"]["email"] == "alice@example.com"
    expires = datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00"))
    assert expires.replace(tzinfo=expires.tzinfo or UTC) == clock.now + timedelta(days=30)
    assert await validate(auth_client, body["token"]) == 204
    assert await validate(auth_client, first) == 204


async def test_wrong_password_and_unknown_email_get_the_same_401(auth_client, hasher):
    await signed_up(auth_client)
    hasher.calls = 0

    wrong = await login(auth_client, password="not the password")
    unknown = await login(auth_client, email="bob@example.com")

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "invalid email or password"}
    assert wrong.headers["www-authenticate"] == unknown.headers["www-authenticate"] == "Bearer"
    # The unknown email is verified against the dummy hash (made once, then 1 each).
    assert hasher.calls == 3


async def test_login_rehashes_a_password_made_with_older_parameters(
    auth_client, db_session_maker, monkeypatch
):
    monkeypatch.setattr(passwords, "_hasher", CountingHasher())
    await signed_up(auth_client)
    monkeypatch.setattr(passwords, "_hasher", PasswordHasher())

    assert (await login(auth_client)).status_code == 200

    async with db_session_maker() as session:
        stored = (await session.scalars(select(User))).one().password_hash
    assert not PasswordHasher().check_needs_rehash(stored)
    assert (await login(auth_client)).status_code == 200


async def test_login_deletes_every_expired_token(auth_client, db_session_maker, clock):
    await signed_up(auth_client)
    await signed_up(auth_client, "bob@example.com")  # never comes back
    clock.now += timedelta(days=31)

    r = await login(auth_client)

    assert r.status_code == 200
    (row,) = await token_rows(db_session_maker)
    assert row.token_hash == hashlib.sha256(r.json()["token"].encode()).hexdigest()


async def test_an_invalid_login_email_is_not_looked_up(auth_client, hasher, monkeypatch):
    looked_up: list[str] = []
    get_by_email = UserRepo.get_by_email

    async def spy(self, email):
        looked_up.append(email)
        return await get_by_email(self, email)

    monkeypatch.setattr(UserRepo, "get_by_email", spy)

    # A NUL would make Postgres refuse the query (a 500); it matches no account.
    r = await login(auth_client, email="alice\x00@example.com")

    assert r.status_code == 401
    assert r.json() == {"detail": "invalid email or password"}
    assert looked_up == []
    assert hasher.calls == 2  # the dummy hash, made and verified: as long as a real login


async def test_the_dummy_hash_is_made_at_startup(auth_client, hasher, monkeypatch):
    monkeypatch.setattr(main, "get_ai_client", lambda: None)

    async with main.lifespan(app):
        assert passwords._dummy_hash is not None
        made = hasher.calls

        await login(auth_client, email="nobody@example.com")

    assert made == 1
    assert hasher.calls == 2  # the login only verified


# --- tokens -------------------------------------------------------------------------


async def test_only_the_tokens_hash_is_stored(auth_client, db_session_maker):
    token = await signed_up(auth_client)

    (row,) = await token_rows(db_session_maker)
    assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()


async def test_a_real_token_reaches_the_feature_routes(auth_client):
    token = await signed_up(auth_client)

    r = await auth_client.get("/api/v1/users/me/consent", headers=bearer(token))

    assert r.status_code == 200
    assert r.json()["consent_required"] is True


@pytest.mark.parametrize(
    "headers",
    [{}, bearer("rdo_unknown"), {"Authorization": "Basic YWxpY2U6cHc="}, bearer("")],
)
async def test_a_missing_or_unknown_token_is_401(auth_client, headers):
    await signed_up(auth_client)

    r = await auth_client.get("/api/v1/dreams", headers=headers)

    assert r.status_code == 401
    assert r.json() == {"detail": "not authenticated"}
    assert r.headers["www-authenticate"] == "Bearer"


async def test_an_expired_token_is_401_and_deleted(auth_client, db_session_maker, clock):
    token = await signed_up(auth_client)
    clock.now += timedelta(days=30, seconds=1)

    r = await auth_client.get("/api/v1/dreams", headers=bearer(token))

    assert r.status_code == 401
    assert r.json() == {"detail": "not authenticated"}
    assert await token_rows(db_session_maker) == []


async def test_a_token_is_renewed_on_use_at_most_hourly(auth_client, db_session_maker, clock):
    token = await signed_up(auth_client)
    issued = clock.now

    clock.now = issued + timedelta(minutes=59)
    assert await validate(auth_client, token) == 204
    (row,) = await token_rows(db_session_maker)
    assert row.expires_at.replace(tzinfo=UTC) == issued + timedelta(days=30)

    clock.now = issued + timedelta(days=29)
    assert await validate(auth_client, token) == 204
    (row,) = await token_rows(db_session_maker)
    assert row.last_used_at.replace(tzinfo=UTC) == clock.now
    assert row.expires_at.replace(tzinfo=UTC) == clock.now + timedelta(days=30)

    # Alive past the first 30 days, because it was used.
    clock.now = issued + timedelta(days=45)
    assert await validate(auth_client, token) == 204


async def test_a_token_revoked_while_being_renewed_is_401(auth_client, db_path, monkeypatch):
    token = await signed_up(auth_client)
    later = datetime.now(UTC) + timedelta(hours=2)  # due for renewal

    def revoked_meanwhile() -> datetime:
        # A logout or password change commits between the lookup and the renewal.
        with sqlite3.connect(db_path) as conn:
            conn.execute("DELETE FROM auth_tokens")
        return later

    monkeypatch.setattr(auth_token_repo, "_utcnow", revoked_meanwhile)

    assert await validate(auth_client, token) == 401


async def test_logout_revokes_only_the_presented_token(auth_client):
    token = await signed_up(auth_client)
    other = (await login(auth_client)).json()["token"]

    r = await auth_client.post("/api/v1/auth/logout", headers=bearer(token))

    assert r.status_code == 204
    assert await validate(auth_client, token) == 401
    assert await validate(auth_client, other) == 204


# --- password and email change --------------------------------------------------------


async def test_password_change_needs_the_current_password(auth_client):
    token = await signed_up(auth_client)

    r = await auth_client.post(
        "/api/v1/auth/password",
        json={"current_password": "not the password", "new_password": "a new password"},
        headers=bearer(token),
    )

    assert r.status_code == 403
    assert r.json() == {"detail": "wrong password"}
    assert (await login(auth_client)).status_code == 200


async def test_password_change_signs_out_every_other_session(auth_client):
    token = await signed_up(auth_client)
    other = (await login(auth_client)).json()["token"]

    r = await auth_client.post(
        "/api/v1/auth/password",
        json={"current_password": PASSWORD, "new_password": "a new password"},
        headers=bearer(token),
    )

    assert r.status_code == 204
    assert await validate(auth_client, token) == 204
    assert await validate(auth_client, other) == 401
    assert (await login(auth_client)).status_code == 401
    assert (await login(auth_client, password="a new password")).status_code == 200


async def test_password_change_refuses_a_short_new_password(auth_client):
    token = await signed_up(auth_client)

    r = await auth_client.post(
        "/api/v1/auth/password",
        json={"current_password": PASSWORD, "new_password": "short"},
        headers=bearer(token),
    )

    assert r.status_code == 422


async def test_email_change(auth_client):
    token = await signed_up(auth_client)
    await signed_up(auth_client, "bob@example.com")

    def change(email: str, password: str = PASSWORD):
        return auth_client.post(
            "/api/v1/auth/email",
            json={"current_password": password, "new_email": email},
            headers=bearer(token),
        )

    assert (await change("carol@example.com", "not the password")).status_code == 403
    taken = await change("Bob@Example.com")
    assert taken.status_code == 409
    assert taken.json() == {"detail": "email already registered"}
    assert (await change("no email")).status_code == 422

    r = await change(" Carol@Example.com ")

    assert r.status_code == 200
    assert r.json()["email"] == "carol@example.com"
    assert (await login(auth_client, email="carol@example.com")).status_code == 200
    assert (await login(auth_client)).status_code == 401
    assert await validate(auth_client, token) == 204


async def test_auth_validation_errors_do_not_echo_the_input(auth_client):
    secret = "seven77"

    short = await register(auth_client, password=secret, invite_code="i" * 257)
    long = await login(auth_client, password=secret * 200)

    assert short.status_code == long.status_code == 422
    for r in (short, long):
        assert secret not in r.text
        assert all("input" not in error for error in r.json()["detail"])
    assert {tuple(e["loc"]) for e in short.json()["detail"]} == {
        ("body", "password"),
        ("body", "invite_code"),
    }


async def test_the_supabase_password_reset_is_gone(auth_client):
    r = await auth_client.post("/api/v1/auth/password-reset", json={"email": "a@example.com"})

    assert r.status_code in (404, 405)


# --- rate limits --------------------------------------------------------------------


async def test_ten_failed_logins_lock_the_email_for_15_minutes(auth_client, hasher, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(limits, "_monotonic", lambda: now[0])
    await signed_up(auth_client)
    for _ in range(10):
        assert (await login(auth_client, password="not the password")).status_code == 401
    hasher.calls = 0

    r = await login(auth_client)  # the right password, but locked

    assert r.status_code == 429
    assert r.headers["retry-after"] == "900"
    assert hasher.calls == 0  # refused before any hashing
    assert (await login(auth_client, email="bob@example.com")).status_code == 401

    now[0] += 600
    assert (await login(auth_client)).headers["retry-after"] == "300"
    now[0] += 300
    assert (await login(auth_client)).status_code == 200


async def test_a_burst_of_wrong_logins_gets_ten_guesses(auth_client, hasher):
    """Concurrent guesses from many addresses count while still in flight."""
    await signed_up(auth_client)
    hasher.calls = 0
    clients = [client_from(f"198.51.100.{i}") for i in range(30)]

    responses = await asyncio.gather(
        *(login(c, password=f"guess {i}") for i, c in enumerate(clients))
    )
    for c in clients:
        await c.aclose()

    codes = sorted(r.status_code for r in responses)
    assert codes == [401] * 10 + [429] * 20
    assert hasher.calls == 10


async def test_a_successful_login_does_not_count_as_a_failure(auth_client, hasher):
    await signed_up(auth_client)
    for _ in range(9):
        assert (await login(auth_client, password="not the password")).status_code == 401
    for _ in range(3):
        assert (await login(auth_client)).status_code == 200

    assert (await login(auth_client, password="not the password")).status_code == 401
    assert (await login(auth_client)).status_code == 429


async def test_thirty_logins_per_ip_per_15_minutes(auth_client, hasher, db_session_maker):
    for i in range(30):
        assert (await login(auth_client, email=f"u{i}@example.com")).status_code == 401
    hasher.calls = 0

    r = await login(auth_client, email="someone@example.com")

    assert r.status_code == 429
    assert int(r.headers["retry-after"]) > 0
    assert hasher.calls == 0
    # Another address is not affected.
    async with client_from("203.0.113.9") as elsewhere:
        assert (await login(elsewhere, email="someone@example.com")).status_code == 401


async def test_five_registrations_per_ip_per_hour(auth_client, hasher, monkeypatch):
    monkeypatch.setattr(limits, "_monotonic", lambda: 1000.0)
    for i in range(4):
        assert (await register(auth_client, email=f"u{i}@example.com")).status_code == 201
    assert (await register(auth_client, invite_code="wrong-invite-code")).status_code == 403
    hasher.calls = 0

    r = await register(auth_client, email="late@example.com")

    assert r.status_code == 429
    assert r.headers["retry-after"] == "3600"
    assert hasher.calls == 0


async def test_ten_wrong_current_passwords_lock_password_and_email_change(auth_client, hasher):
    token = await signed_up(auth_client)
    for _ in range(5):
        r = await auth_client.post(
            "/api/v1/auth/password",
            json={"current_password": "not the password", "new_password": "a new password"},
            headers=bearer(token),
        )
        assert r.status_code == 403
        r = await auth_client.post(
            "/api/v1/auth/email",
            json={"current_password": "not the password", "new_email": "x@example.com"},
            headers=bearer(token),
        )
        assert r.status_code == 403
    hasher.calls = 0

    r = await auth_client.post(
        "/api/v1/auth/password",
        json={"current_password": PASSWORD, "new_password": "a new password"},
        headers=bearer(token),
    )

    assert r.status_code == 429
    assert "retry-after" in r.headers
    assert hasher.calls == 0


@pytest.mark.parametrize("path", ["/api/v1/auth/password", "/api/v1/auth/email"])
async def test_a_burst_of_wrong_current_passwords_gets_ten_guesses(auth_client, hasher, path):
    token = await signed_up(auth_client)
    hasher.calls = 0
    change = {"new_password": "a new password"} if path.endswith("password") else {}
    body = {"current_password": "not the password", "new_email": "x@example.com"} | change

    responses = await asyncio.gather(
        *(auth_client.post(path, json=body, headers=bearer(token)) for _ in range(25))
    )

    assert sorted(r.status_code for r in responses) == [403] * 10 + [429] * 15
    assert hasher.calls == 10


def test_retry_after_waits_until_the_count_is_under_the_limit(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(limits, "_monotonic", lambda: now[0])
    limiter = limits.RateLimiter(2, timedelta(seconds=60))
    for t in (0.0, 10.0, 20.0):  # one more than the limit (record() does not check)
        now[0] = t
        limiter.record("k")

    # Under the limit again once the events at 0 and 10 have aged out.
    assert limiter.retry_after("k") == 50
    now[0] = 69.0
    assert limiter.retry_after("k") == 1
    now[0] = 70.0
    assert limiter.retry_after("k") is None


def test_a_reservation_is_forgiven(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(limits, "_monotonic", lambda: now[0])
    limiter = limits.RateLimiter(2, timedelta(seconds=60))
    first = limiter.reserve("k")
    now[0] = 5.0
    limiter.reserve("k")
    with pytest.raises(Exception) as exc:
        limiter.reserve("k")
    assert getattr(exc.value, "status_code", None) == 429

    limiter.forgive("k", first)

    assert limiter.retry_after("k") is None
    limiter.forgive("unknown", first)  # no-op
    limiter.forgive("k", 999.0)  # no-op
    limiter.reserve("k")
    assert limiter.retry_after("k") == 60  # both live events are from t=5


@pytest.mark.parametrize(
    ("host", "key"),
    [
        ("192.0.2.1", "192.0.2.1"),
        ("2001:db8::1", "2001:db8::/64"),
        ("2001:db8::ffff:abcd:1", "2001:db8::/64"),
        ("2001:db8:0:1::1", "2001:db8:0:1::/64"),
        ("::ffff:192.0.2.1", "192.0.2.1"),
        ("testclient", "testclient"),
    ],
)
def test_client_addresses_count_per_ipv4_address_and_ipv6_64(host, key):
    request = SimpleNamespace(client=SimpleNamespace(host=host))

    assert limits.client_ip(request) == key  # type: ignore[arg-type]


def test_a_limiter_tracks_a_bounded_number_of_keys(monkeypatch):
    monkeypatch.setattr(limits, "_monotonic", lambda: 50.0)
    limiter = limits.RateLimiter(1, timedelta(minutes=1), max_keys=3)
    for key in ("a", "b", "c", "d"):
        limiter.record(key)

    assert len(limiter) == 3
    assert limiter.retry_after("a") is None  # the oldest key was forgotten
    assert limiter.retry_after("d") == 60
    assert {shipped.max_keys for shipped in limits._LIMITERS} == {10_000}


# --- argon2 -------------------------------------------------------------------------


async def test_at_most_two_hashes_or_verifications_run_at_once(monkeypatch):
    running = 0
    peak = 0
    lock = threading.Lock()

    def slow() -> None:
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.05)
        with lock:
            running -= 1

    class SlowHasher(PasswordHasher):
        def hash(self, password, *, salt=None):
            slow()
            return "$argon2id$fake"

        def verify(self, hash, password):
            slow()
            return True

    assert passwords._CONCURRENCY == 2
    monkeypatch.setattr(passwords, "_hasher", SlowHasher())
    # The module's semaphore, made anew in this test's event loop.
    monkeypatch.setattr(passwords, "_slots", asyncio.Semaphore(passwords._CONCURRENCY))

    await asyncio.gather(
        *(passwords.hash_password("pw") for _ in range(3)),
        *(passwords.verify_password("$argon2id$fake", "pw") for _ in range(3)),
    )

    assert peak == 2


# --- the database -------------------------------------------------------------------


async def test_two_emails_differing_only_in_case_are_one_account(db_session_maker):
    async with db_session_maker() as session:
        session.add(User(email="Alice@example.com", password_hash="x"))
        await session.commit()
        session.add(User(email="alice@EXAMPLE.com", password_hash="x"))

        with pytest.raises(IntegrityError):
            await session.commit()


def test_the_engine_keeps_sql_parameters_out_of_errors():
    # Token hashes and password hashes are statement parameters.
    assert db_session.engine.sync_engine.hide_parameters is True
