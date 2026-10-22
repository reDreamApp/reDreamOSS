"""Dream repository."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.dream import Dream


class DreamRepo:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_for_user(self, user_id: UUID, *, limit: int = 200) -> list[Dream]:
        stmt = (
            select(Dream)
            .where(Dream.user_id == user_id)
            .order_by(Dream.created_at.desc())
            .limit(limit)
        )
        return list((await self.db.scalars(stmt)).all())

    async def get_for_user(self, user_id: UUID, dream_id: int) -> Dream | None:
        stmt = select(Dream).where(Dream.id == dream_id, Dream.user_id == user_id)
        return (await self.db.scalars(stmt)).first()

    async def get_for_user_by_session(self, user_id: UUID, session_id: str) -> Dream | None:
        stmt = select(Dream).where(
            Dream.user_id == user_id, Dream.session_id == session_id
        )
        return (await self.db.scalars(stmt)).first()

    async def create(self, dream: Dream) -> Dream:
        self.db.add(dream)
        await self.db.commit()
        await self.db.refresh(dream)
        return dream

    async def save(self, dream: Dream) -> Dream:
        await self.db.commit()
        await self.db.refresh(dream)
        return dream

    async def delete(self, dream: Dream) -> None:
        await self.db.delete(dream)
        await self.db.commit()

    async def clear_current_for_user(self, user_id: UUID) -> None:
        """Mark every dream for `user_id` as not-current. Use before setting one as current."""
        await self.db.execute(
            update(Dream).where(Dream.user_id == user_id).values(is_current_dream=False)
        )
        await self.db.commit()
