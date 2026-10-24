"""Dream model."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    Text,
    false,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ..types import UTCDateTime
from ._types import JsonCol, PkBigInt


class Dream(Base):
    __tablename__ = "dreams"
    __table_args__ = (
        CheckConstraint("source IN ('manual', 'chatbot')", name="dreams_source_check"),
        CheckConstraint("language IN ('en', 'de')", name="dreams_language_check"),
        CheckConstraint(
            "rehearsal_language IN ('en', 'de')", name="dreams_rehearsal_language_check"
        ),
        CheckConstraint("intensity BETWEEN 0 AND 10", name="dreams_intensity_check"),
    )

    id: Mapped[int] = mapped_column(PkBigInt, primary_key=True, autoincrement=True)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    original_dream: Mapped[str] = mapped_column(Text, nullable=False)
    rewritten_dream: Mapped[str | None] = mapped_column(Text)
    intensity: Mapped[int | None] = mapped_column(Integer)
    is_nightmare: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False, index=True
    )
    last_edited: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )
    practice_timestamps: Mapped[list[Any]] = mapped_column(
        JsonCol, nullable=False, default=list, server_default=text("'[]'")
    )
    is_current_dream: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    image_url: Mapped[str | None] = mapped_column(Text)
    session_id: Mapped[str | None] = mapped_column(Text, index=True)
    source: Mapped[str] = mapped_column(
        Text, nullable=False, default="manual", server_default="manual", index=True
    )
    # en|de: the chat's language at the summary (chatbot dreams) or the app's
    # language at creation (manual dreams); NULL for manual dreams created
    # without one.
    language: Mapped[str | None] = mapped_column(Text)

    # Store key under AUDIO_DIR (redream_ai.audio.store); served as a signed link.
    rehearsal_audio_key: Mapped[str | None] = mapped_column(Text)
    rehearsal_script: Mapped[str | None] = mapped_column(Text)
    rehearsal_duration: Mapped[str | None] = mapped_column(Text)
    rehearsal_language: Mapped[str | None] = mapped_column(Text)
    rehearsal_generated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
