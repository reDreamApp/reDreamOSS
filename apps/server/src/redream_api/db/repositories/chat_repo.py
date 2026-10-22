"""Chat repository."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.chat import Chat


class ChatRepo:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_for_user(self, user_id: UUID, *, limit: int = 200) -> list[Chat]:
        stmt = (
            select(Chat)
            .where(Chat.user_id == user_id)
            .order_by(Chat.created_at.desc())
            .limit(limit)
        )
        return list((await self.db.scalars(stmt)).all())

    async def get_for_user(self, user_id: UUID, chat_id: int) -> Chat | None:
        stmt = select(Chat).where(Chat.id == chat_id, Chat.user_id == user_id)
        return (await self.db.scalars(stmt)).first()

    async def create(self, *, user_id: UUID, language: str = "en") -> Chat:
        chat = Chat(user_id=user_id, language=language)
        self.db.add(chat)
        await self.db.commit()
        await self.db.refresh(chat)
        return chat

    async def save(self, chat: Chat) -> Chat:
        await self.db.commit()
        await self.db.refresh(chat)
        return chat

    async def delete(self, chat: Chat) -> None:
        await self.db.delete(chat)
        await self.db.commit()
