"""UserConsent — explicit-consent proof log (GDPR Art. 9(2)(a) + Art. 7(1)).

Append-only: grants and withdrawals are rows, never updates; the current
status is the latest row per (user, consent_key).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base

HEALTH_DATA_CONSENT_KEY = "health_data_processing"


class UserConsent(Base):
    __tablename__ = "user_consents"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    consent_key: Mapped[str] = mapped_column(
        Text, nullable=False, default=HEALTH_DATA_CONSENT_KEY
    )
    version: Mapped[str] = mapped_column(Text, nullable=False)
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
