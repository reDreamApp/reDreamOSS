"""Column types shared by the models."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """TIMESTAMP WITH TIME ZONE that always comes back zone-aware, in UTC.

    Postgres keeps the zone. SQLite (personal mode) stores text without one,
    and its CURRENT_TIMESTAMP defaults are UTC: read back, they would be naive
    and leave the API without an offset, so the app showed UTC as local time."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is not None and value.tzinfo is not None and dialect.name == "sqlite":
            return value.astimezone(UTC)
        return value

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
