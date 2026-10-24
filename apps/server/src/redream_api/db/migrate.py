"""Schema migration runner: applies migrations/*.sql in name order.

    python -m redream_api.db.migrate

The container runs it before uvicorn starts; locally, from apps/server:
uv run --env-file ../../.env python -m redream_api.db.migrate (or make
db-migrate from the repo root). Reads DATABASE_URL
from the environment and nothing else (no Settings: INVITE_CODE and the rest
are not needed to migrate), and reads it as the app's engine does (SQLAlchemy's
asyncpg dialect: ?ssl=require and the like work for both). Waits up to 30 s
for the database, then holds an advisory lock for the whole run, so
concurrent runners apply each file once. Needs a direct or session-mode
connection, not a transaction pooler.

Each file runs together with its schema_migrations row in one transaction,
so a file must not contain BEGIN/COMMIT (refused before anything runs). An
applied file is never run again; one whose sha256 changed since stops the
run (exit 1). Output never contains the URL.

A sqlite+aiosqlite:// URL (personal mode) gets its tables from the ORM models,
which mirror the migrations (tests/pg checks that), and keeps the number of
the newest migration in PRAGMA user_version. A later migration upgrades an
existing SQLite file with its own step in SQLITE_STEPS (a test requires one).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import asyncpg
from sqlalchemy.dialects.postgresql.asyncpg import dialect as _AsyncpgDialect
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError

# apps/server/migrations in the repo; /app/migrations in the image.
MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"
# Any fixed bigint, the same for every runner of this schema.
LOCK_KEY = 4_412_503_871_209_337
_WAIT_SECONDS = 30
_RETRY_SECONDS = 1.0
_SCHEMES = ("postgresql+asyncpg", "postgresql", "postgres")
# Query options SQLAlchemy's dialect consumes itself; asyncpg.connect() has none of them.
_DIALECT_ONLY = ("prepared_statement_cache_size", "prepared_statement_name_func", "async_fallback")
# Blanked before looking for transaction control: literals, quoted names,
# comments and $tag$ bodies (a DO block's BEGIN … END is fine).
_OPAQUE = re.compile(r"\$(\w*)\$.*?\$\1\$|'(?:[^']|'')*'|\"[^\"]*\"|--[^\n]*|/\*.*?\*/", re.S)
_TXN_CONTROL = re.compile(
    r"(?:^|;)\s*(BEGIN|COMMIT|END|ROLLBACK|ABORT|START\s+TRANSACTION|SAVEPOINT|RELEASE"
    r"|PREPARE\s+TRANSACTION)\b",
    re.I,
)


class MigrationError(Exception):
    """A refusal; the message is printed as is."""


# Migration number -> the SQLite statements that bring an existing personal
# database from the previous number to this one. Migration 0001 has none: a
# new file is created from the models at the newest version.
SQLITE_STEPS: dict[int, tuple[str, ...]] = {}


def _number(name: str) -> int:
    return int(name.split("_", 1)[0])


async def run_sqlite(database_url: str, migrations: list[tuple[str, str, str]]) -> str:
    """Creates or upgrades a SQLite database; returns what was done."""
    from sqlalchemy import event, text
    from sqlalchemy.ext.asyncio import create_async_engine

    from . import models as _models  # noqa: F401 — registers the tables
    from .base import Base

    newest = _number(migrations[-1][0]) if migrations else 0
    engine = create_async_engine(database_url)

    # One real transaction for the whole run: the sqlite3 driver commits DDL
    # statement by statement on its own, so a step that fails half-way would
    # leave its first statements applied under the old version number.
    @event.listens_for(engine.sync_engine, "connect")
    def _no_driver_transactions(dbapi_connection: Any, _record: Any) -> None:
        dbapi_connection.isolation_level = None

    @event.listens_for(engine.sync_engine, "begin")
    def _begin(conn: Any) -> None:
        conn.exec_driver_sql("BEGIN")

    try:
        async with engine.begin() as conn:
            version = (await conn.execute(text("PRAGMA user_version"))).scalar_one()
            tables = (
                await conn.execute(text("SELECT count(*) FROM sqlite_master WHERE type='table'"))
            ).scalar_one()
            if version == 0 and tables == 0:
                await conn.run_sync(Base.metadata.create_all)
                await conn.execute(text(f"PRAGMA user_version = {newest}"))
                return f"created at {newest:04d}"
            if version == 0:
                raise MigrationError("the SQLite file has tables but no schema version")
            if version > newest:
                return f"warning: database at {version:04d}, newer than this checkout"
            for number in range(version + 1, newest + 1):
                step = SQLITE_STEPS.get(number)
                if step is None:
                    raise MigrationError(f"no SQLite step for migration {number:04d}")
                for statement in step:
                    await conn.execute(text(statement))
                await conn.execute(text(f"PRAGMA user_version = {number}"))
            return f"at {newest:04d}" if version == newest else f"upgraded to {newest:04d}"
    finally:
        await engine.dispose()


def connect_args(database_url: str) -> dict[str, Any]:
    """asyncpg.connect() keyword arguments for DATABASE_URL, as the app's engine builds them."""
    try:
        url = make_url(database_url)
        dialect = _AsyncpgDialect()  # type: ignore[no-untyped-call]
        _, kwargs = dialect.create_connect_args(  # type: ignore[no-untyped-call]
            url.set(drivername="postgresql+asyncpg")
        )
    except (ArgumentError, ValueError):
        # Never the URL or the parser's message: either may hold the password.
        raise MigrationError("DATABASE_URL is not a valid postgresql+asyncpg:// URL") from None
    if url.drivername not in _SCHEMES:
        raise MigrationError(
            f"DATABASE_URL must be a postgresql+asyncpg:// URL, got {url.drivername}://…"
        )
    for key in _DIALECT_ONLY:
        kwargs.pop(key, None)
    # As the app (db/session.py): no prepared-statement cache.
    return {"timeout": 5, **kwargs, "statement_cache_size": 0}


async def _connect(kwargs: dict[str, Any]) -> asyncpg.Connection:
    deadline = time.monotonic() + _WAIT_SECONDS
    while True:
        try:
            return await asyncpg.connect(**kwargs)
        except (OSError, TimeoutError, asyncpg.CannotConnectNowError) as exc:
            if time.monotonic() >= deadline:
                raise MigrationError(
                    f"database not reachable after {_WAIT_SECONDS} s ({type(exc).__name__})"
                ) from exc
            await asyncio.sleep(_RETRY_SECONDS)
        except Exception as exc:
            # Wrong password, unknown database, bad option: no retry, and the
            # class only (a message could echo parts of the URL).
            raise MigrationError(f"cannot connect ({type(exc).__name__})") from exc


def load(directory: Path = MIGRATIONS_DIR) -> list[tuple[str, str, str]]:
    """(file name, SQL, sha256 hex) of every migration, in name order."""
    files = sorted(directory.glob("*.sql"))
    if not files:
        raise MigrationError(f"no migrations found in {directory}")
    migrations = []
    for path in files:
        sql = path.read_text(encoding="utf-8")
        # A COMMIT would end the runner's transaction mid-file: half applied, no row.
        control = _TXN_CONTROL.search(_OPAQUE.sub(" ", sql))
        if control:
            raise MigrationError(
                f"{path.name} contains {control.group(1).upper()}: no transaction control "
                "in a migration, the runner wraps each file in its own transaction"
            )
        migrations.append((path.name, sql, hashlib.sha256(sql.encode()).hexdigest()))
    return migrations


async def _apply(
    conn: asyncpg.Connection, migrations: list[tuple[str, str, str]]
) -> tuple[list[str], int, list[str]]:
    await conn.execute("SELECT pg_advisory_lock($1)", LOCK_KEY)
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version text PRIMARY KEY,"
        " checksum text NOT NULL,"
        " applied_at timestamptz NOT NULL DEFAULT now())"
    )
    rows = await conn.fetch("SELECT version, checksum FROM schema_migrations")
    applied_before = {row["version"]: row["checksum"] for row in rows}
    applied: list[str] = []
    skipped = 0
    for name, sql, checksum in migrations:
        known = applied_before.get(name)
        if known == checksum:
            skipped += 1
            continue
        if known is not None:
            raise MigrationError(
                f"{name} changed after it was applied (checksum mismatch); "
                "never edit an applied migration, add a new one"
            )
        try:
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (version, checksum) VALUES ($1, $2)",
                    name,
                    checksum,
                )
        except asyncpg.PostgresError as exc:
            raise MigrationError(f"{name} failed: {type(exc).__name__}: {exc}") from exc
        applied.append(name)
    unknown = sorted(set(applied_before) - {name for name, _, _ in migrations})
    return applied, skipped, unknown


async def run(
    database_url: str, migrations: list[tuple[str, str, str]]
) -> tuple[list[str], int, list[str]]:
    """Applies the pending migrations; returns their names, the already-applied
    count and the applied versions this directory does not have."""
    conn = await _connect(connect_args(database_url))
    try:
        return await _apply(conn, migrations)
    except (asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
        # E.g. no CREATE privilege for schema_migrations.
        raise MigrationError(f"{type(exc).__name__}: {exc}") from exc
    finally:
        # Closing the session releases the advisory lock too.
        await conn.close()


def main(directory: Path = MIGRATIONS_DIR) -> int:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        print("migrate: DATABASE_URL is not set", file=sys.stderr)
        return 1
    if database_url.startswith("sqlite+aiosqlite://"):
        try:
            print(f"migrate: SQLite {asyncio.run(run_sqlite(database_url, load(directory)))}")
        except SQLAlchemyError as exc:
            # The run is one transaction: nothing of it was applied.
            print(f"migrate: SQLite unchanged, {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        except MigrationError as exc:
            print(f"migrate: {exc}", file=sys.stderr)
            return 1
        return 0
    try:
        applied, skipped, unknown = asyncio.run(run(database_url, load(directory)))
    except MigrationError as exc:
        print(f"migrate: {exc}", file=sys.stderr)
        return 1
    if unknown:
        # An older image on a newer schema (a rollback?): allowed, but said.
        print(
            f"migrate: warning: applied but not in this image: {', '.join(unknown)}",
            file=sys.stderr,
        )
    names = f" ({', '.join(applied)})" if applied else ""
    print(f"migrate: applied {len(applied)}{names}, already applied {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
