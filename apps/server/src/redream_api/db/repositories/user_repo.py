"""User repository."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.user import User


class UserRepo:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get(self, user_id: UUID) -> User | None:
        return await self.db.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        """Case-insensitive, like the unique index; `email` already normalized."""
        stmt = select(User).where(func.lower(User.email) == email.lower())
        return (await self.db.scalars(stmt)).first()

    async def create(self, *, email: str, password_hash: str) -> User:
        """Raises IntegrityError when the email is taken."""
        user = User(email=email, password_hash=password_hash)
        self.db.add(user)
        await self.db.commit()
        await self.db.refresh(user)
        return user

    async def save(self, user: User) -> User:
        """Raises IntegrityError when a changed email is taken."""
        await self.db.commit()
        await self.db.refresh(user)
        return user

    async def delete(self, user_id: UUID) -> None:
        """Delete the account; the database cascades to its tokens, dreams,
        chats, consent log and moods."""
        user = await self.db.get(User, user_id)
        if user is not None:
            await self.db.delete(user)
            await self.db.commit()
