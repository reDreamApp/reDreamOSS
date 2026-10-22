"""Async SQLAlchemy engine + session factory."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..core.config import settings

_is_asyncpg = settings.database_url.startswith("postgresql+asyncpg://")

engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
    pool_pre_ping=True,
    connect_args={"statement_cache_size": 0} if _is_asyncpg else {},
)

async_session_maker = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)
