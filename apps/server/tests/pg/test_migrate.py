"""The migration runner (redream_api.db.migrate) on a real Postgres: applies
0001 once, refuses a changed file, keeps each file and its row in one
transaction, serializes concurrent runs and reads DATABASE_URL as the app does."""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from redream_api.db import migrate
from redream_api.db.session import make_engine

SERVER_DIR = Path(__file__).resolve().parents[2]


async def _runner(database_url: str) -> tuple[int, str, str]:
    """python -m redream_api.db.migrate as the container runs it."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "redream_api.db.migrate",
        cwd=SERVER_DIR,
        env={**os.environ, "DATABASE_URL": database_url},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    assert proc.returncode is not None
    return proc.returncode, out.decode().strip(), err.decode().strip()


async def _connect(database_url: str) -> asyncpg.Connection:
    return await asyncpg.connect(**migrate.connect_args(database_url))


async def _versions(database_url: str) -> list[tuple[str, str]]:
    conn = await _connect(database_url)
    try:
        rows = await conn.fetch("SELECT version, checksum FROM schema_migrations ORDER BY 1")
    finally:
        await conn.close()
    return [(row["version"], row["checksum"]) for row in rows]


async def test_the_runner_applies_0001_once(empty_db):
    sql = (migrate.MIGRATIONS_DIR / "0001_initial.sql").read_bytes()

    code, out, err = await _runner(empty_db)
    assert (code, out, err) == (0, "migrate: applied 1 (0001_initial.sql), already applied 0", "")
    assert await _versions(empty_db) == [("0001_initial.sql", hashlib.sha256(sql).hexdigest())]

    code, out, _ = await _runner(empty_db)
    assert (code, out) == (0, "migrate: applied 0, already applied 1")
    assert len(await _versions(empty_db)) == 1


async def test_a_changed_migration_stops_the_run(empty_db, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", empty_db)
    probe = tmp_path / "0001_probe.sql"
    probe.write_text("CREATE TABLE s13_probe (id integer);\n")
    assert await asyncio.to_thread(migrate.main, tmp_path) == 0
    applied = await _versions(empty_db)

    probe.write_text("CREATE TABLE s13_probe (id integer, note text);\n")
    capsys.readouterr()
    assert await asyncio.to_thread(migrate.main, tmp_path) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err.startswith("migrate: 0001_probe.sql changed after it was applied")
    assert await _versions(empty_db) == applied  # nothing re-applied or rewritten


async def test_a_failing_migration_leaves_no_trace(empty_db, tmp_path):
    (tmp_path / "0001_bad.sql").write_text("CREATE TABLE s13_probe (id integer);\nSELEC 1;\n")
    with pytest.raises(migrate.MigrationError, match=r"0001_bad\.sql failed: PostgresSyntaxError"):
        await migrate.run(empty_db, migrate.load(tmp_path))
    conn = await _connect(empty_db)
    try:
        # Nothing of the file stays.
        assert await conn.fetchval("SELECT to_regclass('s13_probe')") is None
        assert await conn.fetchval("SELECT count(*) FROM schema_migrations") == 0
    finally:
        await conn.close()


async def test_a_file_whose_row_fails_is_rolled_back_with_it(empty_db, tmp_path):
    # The file itself succeeds, then the runner's own INSERT of its row hits
    # the row the file wrote: the file's table must go with it.
    (tmp_path / "0001_probe.sql").write_text(
        "CREATE TABLE s13_probe (id integer);\n"
        "INSERT INTO schema_migrations (version, checksum) VALUES ('0001_probe.sql', 'x');\n"
    )
    with pytest.raises(migrate.MigrationError, match=r"0001_probe\.sql failed: UniqueViolation"):
        await migrate.run(empty_db, migrate.load(tmp_path))
    conn = await _connect(empty_db)
    try:
        assert await conn.fetchval("SELECT to_regclass('s13_probe')") is None
        assert await conn.fetchval("SELECT count(*) FROM schema_migrations") == 0
    finally:
        await conn.close()


async def test_a_migration_with_commit_is_refused_before_anything_runs(
    empty_db, tmp_path, monkeypatch, capsys
):
    # Run as is, the COMMIT would keep s13_a, the error drop the rest and the
    # row: every later start would then fail on "s13_a already exists".
    monkeypatch.setenv("DATABASE_URL", empty_db)
    (tmp_path / "0001_probe.sql").write_text(
        "CREATE TABLE s13_a (id integer);\nCOMMIT;\nCREATE TABLE s13_b (id integer);\nSELECT 1/0;\n"
    )
    assert await asyncio.to_thread(migrate.main, tmp_path) == 1
    assert capsys.readouterr().err.startswith("migrate: 0001_probe.sql contains COMMIT")
    conn = await _connect(empty_db)
    try:
        assert await conn.fetchval("SELECT to_regclass('s13_a')") is None
    finally:
        await conn.close()


async def test_versions_this_image_does_not_have_are_reported(
    empty_db, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("DATABASE_URL", empty_db)
    (tmp_path / "0001_probe.sql").write_text("CREATE TABLE s13_probe (id integer);\n")
    assert await asyncio.to_thread(migrate.main, tmp_path) == 0
    conn = await _connect(empty_db)
    try:
        await conn.execute("INSERT INTO schema_migrations VALUES ('0002_newer.sql', 'x')")
    finally:
        await conn.close()
    capsys.readouterr()
    assert await asyncio.to_thread(migrate.main, tmp_path) == 0
    out, err = capsys.readouterr()
    assert out == "migrate: applied 0, already applied 1\n"
    assert err == "migrate: warning: applied but not in this image: 0002_newer.sql\n"


async def test_the_runner_and_the_app_read_the_same_url(empty_db):
    # SQLAlchemy's asyncpg options (ssl= is its TLS switch): the app takes
    # them as connect() arguments, so the runner must too.
    url = (
        make_url(empty_db)
        .update_query_dict(
            {"ssl": "disable", "prepared_statement_cache_size": "0", "command_timeout": "60"}
        )
        .render_as_string(hide_password=False)
    )
    code, out, err = await _runner(url)
    assert (code, out, err) == (0, "migrate: applied 1 (0001_initial.sql), already applied 0", "")

    engine = make_engine(url)
    try:
        async with engine.connect() as conn:
            assert await conn.scalar(text("SELECT count(*) FROM schema_migrations")) == 1
    finally:
        await engine.dispose()


async def test_concurrent_runners_apply_each_file_once(empty_db):
    # Hold the runners' advisory lock until both wait for it, so they really
    # start at the same moment.
    conn = await _connect(empty_db)
    try:
        await conn.execute("SELECT pg_advisory_lock($1)", migrate.LOCK_KEY)
        runs = [asyncio.create_task(_runner(empty_db)) for _ in range(2)]
        for _ in range(100):
            waiting = await conn.fetchval(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted"
            )
            if waiting == 2:
                break
            await asyncio.sleep(0.05)
        assert waiting == 2
        await conn.execute("SELECT pg_advisory_unlock($1)", migrate.LOCK_KEY)
        results = await asyncio.gather(*runs)
    finally:
        await conn.close()

    assert sorted(results) == [
        (0, "migrate: applied 0, already applied 1", ""),
        (0, "migrate: applied 1 (0001_initial.sql), already applied 0", ""),
    ]
    assert [version for version, _ in await _versions(empty_db)] == ["0001_initial.sql"]


async def test_a_failed_connect_shows_neither_url_nor_password(empty_db):
    url = (
        make_url(empty_db)
        .set(database="s13_no_such_db", password="s3cret-pw")
        .render_as_string(hide_password=False)
    )
    code, out, err = await _runner(url)
    assert code == 1
    assert out == ""
    assert err.startswith("migrate: cannot connect (")
    assert "s13_no_such_db" not in err
    assert "s3cret-pw" not in err
