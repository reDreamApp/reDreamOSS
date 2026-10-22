"""UserConsent repository — append-only consent proof log (Art. 9/Art. 7)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.user_consent import HEALTH_DATA_CONSENT_KEY, UserConsent


class ConsentRepo:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def latest(
        self, user_id: UUID, *, consent_key: str = HEALTH_DATA_CONSENT_KEY
    ) -> UserConsent | None:
        """The user's current consent status = the newest log row for the key."""
        stmt = (
            select(UserConsent)
            .where(
                UserConsent.user_id == user_id,
                UserConsent.consent_key == consent_key,
            )
            .order_by(UserConsent.id.desc())
            .limit(1)
        )
        return (await self.db.scalars(stmt)).first()

    async def record(
        self,
        user_id: UUID,
        *,
        version: str,
        granted: bool,
        consent_key: str = HEALTH_DATA_CONSENT_KEY,
    ) -> UserConsent:
        """Appends a grant/withdrawal row — never updates existing proof rows."""
        row = UserConsent(
            user_id=user_id,
            consent_key=consent_key,
            version=version,
            granted=granted,
        )
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return row
