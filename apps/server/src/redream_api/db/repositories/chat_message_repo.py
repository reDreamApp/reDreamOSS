"""ChatMessage repository."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.chat_message import ChatMessage


class ChatMessageRepo:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_for_chat(self, chat_id: int, *, limit: int = 500) -> list[ChatMessage]:
        stmt = (
            select(ChatMessage)
            .where(ChatMessage.chat_id == chat_id)
            .order_by(ChatMessage.created_at.asc())
            .limit(limit)
        )
        return list((await self.db.scalars(stmt)).all())

    async def create(self, message: ChatMessage) -> ChatMessage:
        self.db.add(message)
        await self.db.commit()
        await self.db.refresh(message)
        return message

    async def delete(self, message: ChatMessage) -> None:
        await self.db.delete(message)
        await self.db.commit()
