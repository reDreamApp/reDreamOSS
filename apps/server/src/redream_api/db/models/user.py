"""User model — the account: login email, password hash, profile fields."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Index, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    # Stored stripped and lower-cased (services/accounts.normalize_email);
    # unique case-insensitively through the index below.
    email: Mapped[str] = mapped_column(Text, nullable=False)
    # argon2id (core/passwords).
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    gender: Mapped[str | None] = mapped_column(Text)
    age_range: Mapped[str | None] = mapped_column(Text)
    nightmare_frequency: Mapped[str | None] = mapped_column(Text)


Index("users_email_lower_key", func.lower(User.email), unique=True)
