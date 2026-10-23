"""The operator CLI (python -m redream_api.cli): create-user and set-password."""

from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

from redream_api import cli
from redream_api.core.config import settings

PASSWORD = "an operator password"


@pytest.fixture
def stdin(monkeypatch):
    def feed(text: str) -> None:
        monkeypatch.setattr(sys, "stdin", io.StringIO(text))

    return feed


async def login(client, email: str, password: str = PASSWORD) -> int:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    return r.status_code


async def test_create_user_then_login(auth_client, db_session_maker, stdin, capsys, monkeypatch):
    monkeypatch.setattr(settings, "registration_mode", "closed")  # the CLI works anyway
    stdin(PASSWORD + "\n")

    code = await cli.run(["create-user", " Op@Example.com", "--password-stdin"], db_session_maker)

    assert code == 0
    out = capsys.readouterr()
    assert "created op@example.com" in out.out
    assert PASSWORD not in out.out + out.err
    assert await login(auth_client, "op@example.com") == 200


async def test_create_user_asks_twice(auth_client, db_session_maker, monkeypatch, capsys):
    answers = iter([PASSWORD, PASSWORD + "!"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: next(answers))

    assert await cli.run(["create-user", "op@example.com"], db_session_maker) == 1
    assert "do not match" in capsys.readouterr().err

    answers = iter([PASSWORD, PASSWORD])
    assert await cli.run(["create-user", "op@example.com"], db_session_maker) == 0
    assert await login(auth_client, "op@example.com") == 200


@pytest.mark.parametrize("abort", [EOFError, KeyboardInterrupt])
async def test_an_aborted_password_prompt_exits_1(db_session_maker, monkeypatch, capsys, abort):
    def prompt(_: str) -> str:
        raise abort

    monkeypatch.setattr(cli.getpass, "getpass", prompt)

    assert await cli.run(["create-user", "op@example.com"], db_session_maker) == 1
    assert capsys.readouterr().err == "error: no password given\n"


@pytest.mark.parametrize(
    ("email", "password", "message"),
    [
        ("taken@example.com", PASSWORD, "email already registered"),
        ("TAKEN@example.com", PASSWORD, "email already registered"),
        ("new@example.com", "short", "at least 8 characters"),
        ("new@example.com", "x" * 1025, "at most 1024 characters"),
        ("not an email", PASSWORD, "invalid email"),
    ],
)
async def test_create_user_refusals_exit_1(
    db_session_maker, stdin, capsys, email, password, message
):
    stdin(PASSWORD + "\n")
    assert (
        await cli.run(["create-user", "taken@example.com", "--password-stdin"], db_session_maker)
        == 0
    )
    capsys.readouterr()
    stdin(password + "\n")

    code = await cli.run(["create-user", email, "--password-stdin"], db_session_maker)

    assert code == 1
    out = capsys.readouterr()
    assert message in out.err
    assert password not in out.out + out.err


async def test_set_password_signs_the_user_out_everywhere(auth_client, db_session_maker, stdin):
    r = await auth_client.post(
        "/api/v1/auth/register",
        json={
            "email": "alice@example.com",
            "password": PASSWORD,
            "invite_code": settings.invite_code,
        },
    )
    token = r.json()["token"]
    stdin("a brand new password\n")

    code = await cli.run(
        ["set-password", "Alice@example.com", "--password-stdin"], db_session_maker
    )

    assert code == 0
    r = await auth_client.post(
        "/api/v1/auth/session/validate", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 401
    assert await login(auth_client, "alice@example.com") == 401
    assert await login(auth_client, "alice@example.com", "a brand new password") == 200


async def test_set_password_for_an_unknown_email_exits_1(db_session_maker, stdin, capsys):
    stdin(PASSWORD + "\n")

    code = await cli.run(
        ["set-password", "nobody@example.com", "--password-stdin"], db_session_maker
    )

    assert code == 1
    assert "no account" in capsys.readouterr().err


def test_the_module_runs_against_database_url(db_engine, db_path: Path, tmp_path: Path):
    """`python -m redream_api.cli` with the app's settings: DATABASE_URL picks the DB."""
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}",
        "PYTHONPATH": str(Path(cli.__file__).parents[1]),
    }

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "redream_api.cli", *args],
            input=PASSWORD + "\n",
            capture_output=True,
            text=True,
            env=env,
            cwd=tmp_path,  # no .env here
            timeout=60,
            check=False,
        )

    first = run("create-user", "op@example.com", "--password-stdin")
    again = run("create-user", "op@example.com", "--password-stdin")

    assert (first.returncode, again.returncode) == (0, 1), first.stderr
    assert "email already registered" in again.stderr
    assert PASSWORD not in first.stdout + first.stderr + again.stdout + again.stderr


async def test_an_unusable_database_exits_1_without_details(stdin, capsys, tmp_path: Path):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'missing' / 'x.db'}")
    stdin(PASSWORD + "\n")
    try:
        code = await cli.run(
            ["create-user", "op@example.com", "--password-stdin"],
            async_sessionmaker(bind=engine, class_=AsyncSession),
        )
    finally:
        await engine.dispose()

    assert code == 1
    err = capsys.readouterr().err
    assert err.startswith("error: the database could not be used (")
    assert "missing" not in err
