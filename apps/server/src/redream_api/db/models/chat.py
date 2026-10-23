"""Chat model."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Text,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._types import STAGES_SQL, PkBigInt


class Chat(Base):
    __tablename__ = "chats"
    __table_args__ = (
        CheckConstraint("language IN ('en', 'de')", name="chats_language_check"),
        CheckConstraint(f"current_stage IN {STAGES_SQL}", name="chats_current_stage_check"),
    )

    id: Mapped[int] = mapped_column(PkBigInt, primary_key=True, autoincrement=True)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    language: Mapped[str] = mapped_column(Text, nullable=False, default="en", server_default="en")
    # Set when a crisis message switched `language`: that turn had no AI reply,
    # so the next AI turn carries `language_switched` (cleared once it is saved).
    language_switch_pending: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    current_stage: Mapped[str] = mapped_column(
        Text, nullable=False, default="recording", server_default="recording"
    )
    is_done: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    is_nightmare: Mapped[bool | None] = mapped_column(Boolean)
    title: Mapped[str | None] = mapped_column(Text)
    # Generated once by the AI (recording→rewriting); echoed into every later
    # AI call so the stateless service never regenerates it (ADR 0002).
    nightmare_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
