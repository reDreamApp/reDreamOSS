"""AuthToken repository — opaque bearer tokens, stored as their sha256.

A token lives TOKEN_LIFETIME after its last use (sliding): a request renews it
when it was last renewed more than RENEW_AFTER ago, so not every request
writes. An expired token is deleted when it is met, and every login deletes
all expired tokens.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.auth_token import AuthToken
from ..models.user import User

TOKEN_PREFIX = "rdo_"
TOKEN_LIFETIME = timedelta(days=30)
RENEW_AFTER = timedelta(hours=1)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    """SQLite hands timestamps back without a zone; they are stored in UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class AuthTokenRepo:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def issue(self, user_id: UUID) -> tuple[str, AuthToken]:
        """A new token for `user_id`: the plaintext (returned once, never
        stored) and its row."""
        token = new_token()
        now = _utcnow()
        row = AuthToken(
            token_hash=hash_token(token),
            user_id=user_id,
            created_at=now,
            last_used_at=now,
            expires_at=now + TOKEN_LIFETIME,
        )
        self.db.add(row)
        await self.db.commit()
        return token, row

    async def authenticate(self, token_hash: str) -> User | None:
        """The user of a live token, renewing it when due. None for an unknown
        or expired token; an expired one is deleted."""
        found = (
            await self.db.execute(
                select(AuthToken, User)
                .join(User, User.id == AuthToken.user_id)
                .where(AuthToken.token_hash == token_hash)
            )
        ).first()
        if found is None:
            return None
        row, user = found._tuple()
        now = _utcnow()
        if _aware(row.expires_at) <= now:
            await self.revoke(token_hash)
            return None
        if now - _aware(row.last_used_at) > RENEW_AFTER:
            # A statement, not the loaded row: a revoke (logout, password
            # change, account deletion) may have deleted it meanwhile.
            renewed = await self.db.execute(
                update(AuthToken)
                .where(AuthToken.token_hash == token_hash)
                .values(last_used_at=now, expires_at=now + TOKEN_LIFETIME)
            )
            await self.db.commit()
            if cast(CursorResult[Any], renewed).rowcount == 0:
                return None
        return user

    async def revoke(self, token_hash: str) -> None:
        await self.db.execute(delete(AuthToken).where(AuthToken.token_hash == token_hash))
        await self.db.commit()

    async def revoke_all(self, user_id: UUID, *, keep: str | None = None) -> None:
        """Delete every token of `user_id`, except `keep` (a token hash)."""
        stmt = delete(AuthToken).where(AuthToken.user_id == user_id)
        if keep is not None:
            stmt = stmt.where(AuthToken.token_hash != keep)
        await self.db.execute(stmt)
        await self.db.commit()

    async def delete_expired(self) -> None:
        """Delete every expired token (run on each login): those of users who
        never come back too."""
        await self.db.execute(delete(AuthToken).where(AuthToken.expires_at <= _utcnow()))
        await self.db.commit()
