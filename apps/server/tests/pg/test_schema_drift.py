"""migrations/*.sql vs the ORM: the migrated database must be exactly what
Base.metadata.create_all builds.

create_all runs into a scratch schema (drift_orm) of the same database, and
the two schemas are compared through Postgres's own rendering of every
object: format_type, pg_get_expr, pg_get_constraintdef, pg_get_indexdef,
pg_get_triggerdef. So types, NOT NULL, defaults (sequences included),
keys, ON DELETE, CHECK expressions, index columns, methods and predicates all
count, and so does anything extra (a view, a sequence, a trigger, a
function). Any difference fails with one line per mismatch.
"""

from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from redream_api.db import migrate
from redream_api.db import models as _models  # noqa: F401 — register tables
from redream_api.db.base import Base
from redream_api.db.session import make_engine

ORM_SCHEMA = "drift_orm"

# (label, value) of every object in schema :s; the runner's own table aside.
_CATALOG = """
SELECT 'relation ' || c.relname, c.relkind::text
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = :s AND c.relkind NOT IN ('i', 'I') AND c.relname <> 'schema_migrations'
UNION ALL
SELECT 'column ' || c.relname || '.' || a.attname,
       format_type(a.atttypid, a.atttypmod)
       || CASE WHEN a.attnotnull THEN ' NOT NULL' ELSE '' END
       || COALESCE(' DEFAULT ' || pg_get_expr(d.adbin, d.adrelid), '')
       || CASE WHEN a.attidentity <> '' THEN ' IDENTITY' ELSE '' END
       || CASE WHEN a.attgenerated <> '' THEN ' GENERATED' ELSE '' END
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
WHERE n.nspname = :s AND c.relkind IN ('r', 'p') AND a.attnum > 0 AND NOT a.attisdropped
  AND c.relname <> 'schema_migrations'
UNION ALL
SELECT 'constraint ' || c.relname || '.' || con.conname, pg_get_constraintdef(con.oid)
FROM pg_constraint con
JOIN pg_class c ON c.oid = con.conrelid
JOIN pg_namespace n ON n.oid = con.connamespace
WHERE n.nspname = :s AND c.relname <> 'schema_migrations'
UNION ALL
SELECT 'index ' || tablename || '.' || indexname, indexdef
FROM pg_indexes
WHERE schemaname = :s AND tablename <> 'schema_migrations'
UNION ALL
SELECT 'trigger ' || c.relname || '.' || t.tgname, pg_get_triggerdef(t.oid)
FROM pg_trigger t
JOIN pg_class c ON c.oid = t.tgrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = :s AND NOT t.tgisinternal
UNION ALL
SELECT 'function ' || p.proname, p.prosrc
FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = :s
"""


async def _catalog(conn: AsyncConnection, schema: str) -> dict[str, str]:
    rows = await conn.execute(text(_CATALOG), {"s": schema})
    # Postgres qualifies names outside the search path (drift_orm.users).
    unqualified = re.compile(rf"\b{schema}\.")
    return {label: unqualified.sub("", value) for label, value in rows.all()}


async def schema_diff(conn: AsyncConnection) -> list[str]:
    """The migrated public schema vs create_all's, one line per difference."""
    await conn.execute(text(f"DROP SCHEMA IF EXISTS {ORM_SCHEMA} CASCADE"))
    await conn.execute(text(f"CREATE SCHEMA {ORM_SCHEMA}"))
    await conn.run_sync(
        lambda sync: Base.metadata.create_all(
            sync.execution_options(schema_translate_map={None: ORM_SCHEMA})
        )
    )
    migrated = await _catalog(conn, "public")
    orm = await _catalog(conn, ORM_SCHEMA)
    return [
        f"{label}: migrated={migrated.get(label, '(none)')} | orm={orm.get(label, '(none)')}"
        for label in sorted(migrated.keys() | orm.keys())
        if migrated.get(label) != orm.get(label)
    ]


async def _migrated_diff(database_url: str, *statements: str) -> list[str]:
    await migrate.run(database_url, migrate.load())
    engine = make_engine(database_url)
    try:
        async with engine.connect() as conn:
            for statement in statements:
                await conn.exec_driver_sql(statement)
            diff = await schema_diff(conn)
            await conn.rollback()  # drift_orm and the changes go again
            return diff
    finally:
        await engine.dispose()


async def test_the_migrated_schema_matches_the_orm(empty_db):
    diff = await _migrated_diff(empty_db)
    assert not diff, "schema drift:\n" + "\n".join(diff)


async def test_the_drift_check_sees_differences(empty_db):
    """The comparison is not vacuous: each hand-made change shows up, alone."""
    changes: dict[str, str | tuple[str, ...]] = {
        "column dreams.title": "ALTER TABLE dreams ALTER COLUMN title DROP NOT NULL",
        "column chats.language": "ALTER TABLE chats ALTER COLUMN language SET DEFAULT 'EN'",
        "column daily_moods.id": "ALTER TABLE daily_moods ALTER COLUMN id DROP DEFAULT",
        "column auth_tokens.token_hash": (
            "ALTER TABLE auth_tokens ALTER COLUMN token_hash TYPE varchar(80)"
        ),
        "column daily_moods.note": "ALTER TABLE daily_moods ADD COLUMN note text",
        # The same literals, the wrong column.
        "constraint dreams.dreams_language_check": (
            "ALTER TABLE dreams DROP CONSTRAINT dreams_language_check,"
            " ADD CONSTRAINT dreams_language_check CHECK (rehearsal_language IN ('en', 'de'))"
        ),
        "constraint chats.chats_language_check": (
            "ALTER TABLE chats DROP CONSTRAINT chats_language_check,"
            " ADD CONSTRAINT chats_language_check CHECK (language NOT IN ('en', 'de'))"
        ),
        "constraint chat_messages.chat_messages_role_check": (
            "ALTER TABLE chat_messages DROP CONSTRAINT chat_messages_role_check,"
            " ADD CONSTRAINT chat_messages_role_check CHECK (role IN ('user', 'assistant') OR true)"
        ),
        "constraint user_consents.user_consents_user_id_fkey": (
            "ALTER TABLE user_consents DROP CONSTRAINT user_consents_user_id_fkey,"
            " ADD FOREIGN KEY (user_id) REFERENCES users (id)"
        ),
        "index dreams.ix_dreams_session_id": (
            "DROP INDEX ix_dreams_session_id",
            "CREATE INDEX ix_dreams_session_id ON dreams (session_id) WHERE session_id IS NOT NULL",
        ),
        "index dreams.ix_dreams_source": (
            "DROP INDEX ix_dreams_source",
            "CREATE INDEX ix_dreams_source ON dreams USING hash (source)",
        ),
        "function s13_noop": (
            "CREATE FUNCTION s13_noop() RETURNS trigger LANGUAGE plpgsql"
            " AS $$BEGIN RETURN NEW; END$$"
        ),
        "trigger dreams.s13_noop": (
            "CREATE TRIGGER s13_noop BEFORE INSERT ON dreams"
            " FOR EACH ROW EXECUTE FUNCTION s13_noop()"
        ),
        "relation s13_view": "CREATE VIEW s13_view AS SELECT 1 AS one",
    }
    statements = [
        part
        for change in changes.values()
        for part in ((change,) if isinstance(change, str) else change)
    ]
    diff = await _migrated_diff(empty_db, *statements)
    assert [line.split(": ", 1)[0] for line in diff] == sorted(changes), "\n".join(diff)
