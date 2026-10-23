"""pytest fixtures for the real-Postgres tests (migration runner, schema drift,
smoke on the migrated schema).

Skipped unless TEST_DATABASE_URL is set: a postgresql+asyncpg:// URL to an
EMPTY, throwaway database (CI: the postgres-schema job). With REQUIRE_PG=1
(CI, make db-test) a missing URL fails instead. Every test starts by dropping
the tables there and refuses to when one of them holds rows.
"""

from __future__ import annotations

import os

# Set env BEFORE importing redream_api so the global Settings constructs
# cleanly when tests/pg runs alone. setdefault: tests/api's conftest, when it
# ran first, has already pinned these.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("INVITE_CODE", "test-invite-code")
os.environ.setdefault("LLM_BASE_URL", "http://llm.invalid/v1")
os.environ.setdefault("LLM_API_KEY", "dummy")
os.environ.setdefault("LLM_MODEL", "m-large")
os.environ.setdefault("LLM_SMALL_MODEL", "m-small")

from pathlib import Path

import asyncpg
import pytest

from redream_api.core import limits
from redream_api.db.migrate import connect_args

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")


@pytest.fixture(autouse=True)
def _needs_postgres() -> None:
    if not TEST_DATABASE_URL:
        if os.environ.get("REQUIRE_PG") == "1":
            pytest.fail("REQUIRE_PG=1 but TEST_DATABASE_URL is not set")
        pytest.skip("TEST_DATABASE_URL is not set (an empty Postgres database)")


@pytest.fixture(autouse=True)
def _fresh_limits() -> None:
    """No rate-limit or daily-cap counts from other tests (core/limits)."""
    limits.reset()


@pytest.fixture(autouse=True)
def audio_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """AUDIO_DIR for this test: a fresh folder under tmp_path, never the repo."""
    from redream_ai.config import settings as ai_settings

    path = tmp_path.resolve() / "audio"
    monkeypatch.setattr(ai_settings, "audio_dir", path)
    return path


@pytest.fixture
async def empty_db() -> str:
    """TEST_DATABASE_URL, with every table in its public schema dropped (and
    the drift check's scratch schema)."""
    conn = await asyncpg.connect(**connect_args(TEST_DATABASE_URL))
    try:
        tables = [
            row["tablename"]
            for row in await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
        ]
        for table in tables:
            if table != "schema_migrations" and await conn.fetchval(
                f'SELECT EXISTS (SELECT 1 FROM "{table}")'
            ):
                pytest.fail(
                    f"TEST_DATABASE_URL: table {table} holds rows; "
                    "point it at an empty, throwaway database"
                )
        for table in tables:
            await conn.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')
        await conn.execute("DROP SCHEMA IF EXISTS drift_orm CASCADE")
    finally:
        await conn.close()
    return TEST_DATABASE_URL
