"""DailyMood model — one row per (user, date)."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Date, ForeignKey, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ..types import UTCDateTime
from ._types import PkBigInt


class DailyMood(Base):
    __tablename__ = "daily_moods"
    __table_args__ = (
        UniqueConstraint("user_id", "mood_date", name="daily_moods_user_id_mood_date_key"),
    )

    id: Mapped[int] = mapped_column(PkBigInt, primary_key=True, autoincrement=True)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mood_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    emotion: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )
    last_updated: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )
