"""Async SQLAlchemy engine + session factory."""

from __future__ import annotations

from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ..core.config import settings


def make_engine(database_url: str) -> AsyncEngine:
    is_asyncpg = database_url.startswith("postgresql+asyncpg://")
    created = create_async_engine(
        database_url,
        echo=False,
        future=True,
        pool_pre_ping=True,
        # A failed statement's error then leaves out its parameters: token
        # hashes and password hashes stay out of the logs.
        hide_parameters=True,
        connect_args={"statement_cache_size": 0} if is_asyncpg else {},
    )
    if database_url.startswith("sqlite"):

        @event.listens_for(created.sync_engine, "connect")
        def _foreign_keys(dbapi_connection: Any, _record: Any) -> None:
            # ON DELETE CASCADE as on Postgres (account deletion relies on
            # it): SQLite enforces foreign keys only when asked, per connection.
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return created


engine = make_engine(settings.database_url)

async_session_maker = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)
