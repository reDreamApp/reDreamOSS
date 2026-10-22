"""Dream model."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._types import JsonCol, PkBigInt


class Dream(Base):
    __tablename__ = "dreams"

    id: Mapped[int] = mapped_column(PkBigInt, primary_key=True, autoincrement=True)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    original_dream: Mapped[str] = mapped_column(String, nullable=False)
    rewritten_dream: Mapped[str | None] = mapped_column(String)
    intensity: Mapped[int | None] = mapped_column(Integer)
    is_nightmare: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    last_edited: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    practice_timestamps: Mapped[list[Any]] = mapped_column(
        JsonCol, nullable=False, default=list
    )
    is_current_dream: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    image_url: Mapped[str | None] = mapped_column(String)
    session_id: Mapped[str | None] = mapped_column(String, index=True)
    source: Mapped[str] = mapped_column(String, nullable=False, default="manual", index=True)

    rehearsal_audio_url: Mapped[str | None] = mapped_column(String)
    rehearsal_script: Mapped[str | None] = mapped_column(String)
    rehearsal_duration: Mapped[str | None] = mapped_column(String)
    rehearsal_language: Mapped[str | None] = mapped_column(String)
    rehearsal_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
