"""The migration runner's parts that need no Postgres: reading DATABASE_URL
(as the app's engine does, never echoing it), finding and vetting the files,
and waiting for the database. tests/pg runs it against a real one."""

from __future__ import annotations

import asyncpg
import pytest

from redream_api.db import migrate

SECRET = "TopSecretPW"


def test_the_url_is_read_as_the_apps_engine_reads_it():
    kwargs = migrate.connect_args(
        "postgresql+asyncpg://app:pa#ss?/x@db.example:6432/redream"
        "?ssl=require&prepared_statement_cache_size=0&command_timeout=60"
    )
    assert kwargs == {
        "timeout": 5,
        "user": "app",
        "password": "pa#ss?/x",
        "host": "db.example",
        "port": 6432,
        "database": "redream",
        "ssl": "require",
        "command_timeout": "60",
        "statement_cache_size": 0,
    }


@pytest.mark.parametrize("scheme", ["postgresql+asyncpg", "postgresql", "postgres"])
def test_postgres_schemes_are_accepted(scheme):
    kwargs = migrate.connect_args(f"{scheme}://postgres@/postgres?host=/run/pg")
    assert (kwargs["host"], kwargs["database"]) == ("/run/pg", "postgres")


@pytest.mark.parametrize(
    ("url", "scheme"),
    [
        ("sqlite+aiosqlite:///./redream.db", "sqlite+aiosqlite"),
        (f"postgresql+psycopg://postgres:{SECRET}@db/postgres", "postgresql+psycopg"),
    ],
)
def test_other_drivers_are_refused_by_scheme_only(url, scheme):
    with pytest.raises(migrate.MigrationError) as caught:
        migrate.connect_args(url)
    assert (
        str(caught.value) == f"DATABASE_URL must be a postgresql+asyncpg:// URL, got {scheme}://…"
    )


@pytest.mark.parametrize(
    "url",
    [
        f"host=db user=postgres password={SECRET} dbname=postgres",
        f"postgresql+asyncpg:/postgres:{SECRET}@db:5432/postgres",
        f"postgresql+asyncpg//postgres:{SECRET}@db:5432/postgres",
        f"postgres:{SECRET}@db:5432/postgres",
        f"postgresql+asyncpg://postgres:{SECRET}@db:port/postgres",
    ],
)
def test_a_malformed_url_is_never_echoed(url, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", url)
    assert migrate.main() == 1
    out, err = capsys.readouterr()
    assert (out, err) == ("", "migrate: DATABASE_URL is not a valid postgresql+asyncpg:// URL\n")


def test_an_empty_directory_is_an_error(tmp_path):
    with pytest.raises(migrate.MigrationError, match="no migrations found in"):
        migrate.load(tmp_path)


def test_the_shipped_migrations_load():
    assert migrate.load()[0][0] == "0001_initial.sql"


@pytest.mark.parametrize(
    ("sql", "word"),
    [
        ("BEGIN;\nCREATE TABLE t (id int);\nCOMMIT;\n", "BEGIN"),
        ("CREATE TABLE t (id int); commit;", "COMMIT"),
        ("CREATE TABLE t (id int);\nEND;\n", "END"),
        ("CREATE TABLE t (id int);\n  ROLLBACK;", "ROLLBACK"),
        ("START TRANSACTION; CREATE TABLE t (id int);", "START TRANSACTION"),
        ("CREATE TABLE t (id int); SAVEPOINT s;", "SAVEPOINT"),
    ],
)
def test_transaction_control_in_a_file_is_refused(tmp_path, sql, word):
    (tmp_path / "0001_x.sql").write_text(sql)
    with pytest.raises(migrate.MigrationError, match=f"^0001_x.sql contains {word}: "):
        migrate.load(tmp_path)


def test_the_same_words_inside_bodies_literals_and_comments_are_fine(tmp_path):
    (tmp_path / "0001_x.sql").write_text(
        "-- COMMIT is not allowed here\n"
        "/* nor ROLLBACK */\n"
        "CREATE TABLE t (note text DEFAULT 'x; COMMIT', \"end\" int);\n"
        "DO $$BEGIN PERFORM 1; END$$;\n"
        "CREATE FUNCTION f() RETURNS int LANGUAGE plpgsql AS $body$\n"
        "BEGIN\n  RETURN 1;\nEND\n$body$;\n"
    )
    assert [name for name, _, _ in migrate.load(tmp_path)] == ["0001_x.sql"]


async def test_the_runner_waits_for_the_database(monkeypatch):
    attempts: list[int] = []
    connection = object()

    async def connect(**_kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise ConnectionRefusedError
        if len(attempts) == 2:
            raise asyncpg.CannotConnectNowError("the database system is starting up")
        return connection

    monkeypatch.setattr(migrate.asyncpg, "connect", connect)
    monkeypatch.setattr(migrate, "_RETRY_SECONDS", 0)
    assert await migrate._connect({}) is connection
    assert len(attempts) == 3


async def test_the_runner_gives_up_after_the_deadline(monkeypatch):
    async def connect(**_kwargs):
        raise TimeoutError

    monkeypatch.setattr(migrate.asyncpg, "connect", connect)
    monkeypatch.setattr(migrate, "_WAIT_SECONDS", 0)
    message = r"^database not reachable after 0 s \(TimeoutError\)$"
    with pytest.raises(migrate.MigrationError, match=message):
        await migrate._connect({})


async def test_a_refused_login_is_not_retried(monkeypatch):
    attempts: list[int] = []

    async def connect(**_kwargs):
        attempts.append(1)
        raise asyncpg.InvalidPasswordError(f"password authentication failed ({SECRET})")

    monkeypatch.setattr(migrate.asyncpg, "connect", connect)
    with pytest.raises(migrate.MigrationError) as caught:
        await migrate._connect({})
    assert str(caught.value) == "cannot connect (InvalidPasswordError)"
    assert len(attempts) == 1


def test_a_database_error_outside_a_file_is_a_one_line_refusal(monkeypatch, capsys):
    class Conn:
        async def execute(self, *_args):
            raise asyncpg.InsufficientPrivilegeError("permission denied for schema public")

        async def close(self):
            pass

    async def connect(_kwargs):
        return Conn()

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://postgres@db/postgres")
    monkeypatch.setattr(migrate, "_connect", connect)
    assert migrate.main() == 1
    assert capsys.readouterr().err == (
        "migrate: InsufficientPrivilegeError: permission denied for schema public\n"
    )
