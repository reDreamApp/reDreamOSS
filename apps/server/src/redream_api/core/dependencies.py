"""Shared FastAPI dependencies: DB session + authenticated user."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.user import User
from ..db.repositories.auth_token_repo import AuthTokenRepo, hash_token
from ..db.repositories.consent_repo import ConsentRepo
from ..db.session import async_session_maker
from . import consent

_bearer_scheme = HTTPBearer(auto_error=False)

# One answer for a missing, unknown or expired token: it does not say which.
NOT_AUTHENTICATED = "not authenticated"


async def get_db() -> AsyncIterator[AsyncSession]:
    async with async_session_maker() as session:
        yield session


@dataclass(frozen=True)
class CurrentSession:
    """The signed-in user and the hash of the token they presented."""

    user: User
    token_hash: str


async def get_current_session(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> CurrentSession:
    """Look the bearer token up (by its sha256) and renew it when due.

    401 with WWW-Authenticate: Bearer for a missing, unknown or expired token.
    """
    if creds is None:
        raise _unauthorized()
    token_hash = hash_token(creds.credentials)
    user = await AuthTokenRepo(db).authenticate(token_hash)
    if user is None:
        raise _unauthorized()
    return CurrentSession(user=user, token_hash=token_hash)


async def get_current_user(session: CurrentSession = Depends(get_current_session)) -> User:
    """The signed-in user (see get_current_session)."""
    return session.user


def _unauthorized() -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, NOT_AUTHENTICATED, headers={"WWW-Authenticate": "Bearer"}
    )


async def require_consent(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Gate for routes that CREATE or PROCESS health data (Art. 9(2)(a)).

    The user must have granted the CURRENT consent-text version (an older
    grant means the copy materially changed → re-consent). Reads and
    deletion stay ungated: withdrawal must never lock users out of viewing,
    exporting, or erasing their own data (Art. 7(3) withdrawal ≠ erasure).

    403 with detail "consent_required" — the app routes this to the consent
    screen.
    """
    latest = await ConsentRepo(db).latest(user.id)
    if latest is None or not latest.granted or latest.version != consent.CONSENT_VERSION:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "consent_required")
