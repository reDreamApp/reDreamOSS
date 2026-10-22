"""Shared FastAPI dependencies: DB session + authenticated user."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.user import User
from ..db.repositories.consent_repo import ConsentRepo
from ..db.repositories.user_repo import UserRepo
from ..db.session import async_session_maker
from .config import settings
from .supabase_jwt import JwtVerificationError, verify_supabase_jwt

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with async_session_maker() as session:
        yield session


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Verify the bearer token and upsert the corresponding `users` row.

    Raises 401 on missing/invalid token, 503 if JWKS is unreachable.
    """

    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")

    try:
        claims = await verify_supabase_jwt(creds.credentials)
    except JwtVerificationError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — JWKS fetch failure
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, f"auth backend unreachable: {exc}"
        ) from exc

    sub = claims.get("sub")
    if not sub:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token missing sub")

    try:
        user_id = UUID(str(sub))
    except (ValueError, TypeError) as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "sub is not a UUID") from exc

    email = claims.get("email")
    return await UserRepo(db).upsert_from_supabase(user_id=user_id, email=email)


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
    if latest is None or not latest.granted or latest.version != settings.consent_version:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "consent_required")
