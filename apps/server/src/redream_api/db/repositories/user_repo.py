"""User repository."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.user import User


class UserRepo:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get(self, user_id: UUID) -> User | None:
        return await self.db.get(User, user_id)

    async def upsert_from_supabase(self, *, user_id: UUID, email: str | None) -> User:
        existing = await self.db.get(User, user_id)
        if existing is not None:
            if email and existing.email != email:
                existing.email = email
                await self.db.commit()
            return existing

        user = User(id=user_id, email=email)
        self.db.add(user)
        await self.db.commit()
        await self.db.refresh(user)
        return user

    async def delete(self, user_id: UUID) -> None:
        user = await self.db.get(User, user_id)
        if user is not None:
            await self.db.delete(user)
            await self.db.commit()
