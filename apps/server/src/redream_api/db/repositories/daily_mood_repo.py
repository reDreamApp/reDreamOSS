"""DailyMood repository."""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.daily_mood import DailyMood


class DailyMoodRepo:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_for_user_between(
        self, user_id: UUID, *, start: date, end: date
    ) -> list[DailyMood]:
        stmt = (
            select(DailyMood)
            .where(
                DailyMood.user_id == user_id,
                DailyMood.mood_date >= start,
                DailyMood.mood_date <= end,
            )
            .order_by(DailyMood.mood_date.asc())
        )
        return list((await self.db.scalars(stmt)).all())

    async def get_for_user_on_date(
        self, user_id: UUID, mood_date: date
    ) -> DailyMood | None:
        stmt = select(DailyMood).where(
            DailyMood.user_id == user_id, DailyMood.mood_date == mood_date
        )
        return (await self.db.scalars(stmt)).first()

    async def upsert_for_user_date(
        self, *, user_id: UUID, mood_date: date, emotion: str
    ) -> DailyMood:
        existing = await self.get_for_user_on_date(user_id, mood_date)
        if existing is not None:
            existing.emotion = emotion
            existing.last_updated = datetime.now(timezone.utc)
            await self.db.commit()
            await self.db.refresh(existing)
            return existing

        mood = DailyMood(user_id=user_id, mood_date=mood_date, emotion=emotion)
        self.db.add(mood)
        await self.db.commit()
        await self.db.refresh(mood)
        return mood
