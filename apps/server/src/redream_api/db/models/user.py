"""User model — extension of Supabase auth.users."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    email: Mapped[str | None] = mapped_column(String, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    gender: Mapped[str | None] = mapped_column(String)
    age_range: Mapped[str | None] = mapped_column(String)
    nightmare_frequency: Mapped[str | None] = mapped_column(String)
