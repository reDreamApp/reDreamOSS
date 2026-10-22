"""Cross-database type variants.

SQLAlchemy column types that resolve to native Postgres types in production
and to portable equivalents under SQLite (used by tests). This lets us run
the full test suite with `aiosqlite` without dragging in a real Postgres.
"""

from __future__ import annotations

from sqlalchemy import JSON, BigInteger, Integer
from sqlalchemy.dialects.postgresql import JSONB

# JSON column — JSONB on Postgres, JSON on SQLite.
JsonCol = JSONB().with_variant(JSON(), "sqlite")

# Auto-incrementing primary key — BIGINT on Postgres (BIGSERIAL semantics),
# INTEGER on SQLite (only INTEGER PRIMARY KEY triggers SQLite's rowid
# autoincrement; a BIGINT PRIMARY KEY is a plain non-auto column).
PkBigInt = BigInteger().with_variant(Integer(), "sqlite")
