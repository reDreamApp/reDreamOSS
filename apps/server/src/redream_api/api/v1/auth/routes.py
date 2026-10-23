"""Auth routes: register, login, logout, password and email change.

The server keeps its own accounts: argon2id password hashes (core/passwords)
and opaque bearer tokens stored as their sha256 (auth_token_repo), valid
30 days after their last use. REGISTRATION_MODE decides who may register;
the operator's CLI (python -m redream_api.cli) creates users and resets
passwords in every mode.

Rate limits (core/limits) are checked before any password is hashed.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ....core import limits, passwords
from ....core.config import settings
from ....core.dependencies import CurrentSession, get_current_session, get_current_user, get_db
from ....db.models.user import User
from ....db.repositories.auth_token_repo import AuthTokenRepo
from ....services import accounts
from .schemas import (
    AuthOut,
    EmailChangeRequest,
    LoginRequest,
    PasswordChangeRequest,
    RegisterRequest,
    UserOut,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

INVALID_LOGIN = "invalid email or password"
WRONG_PASSWORD = "wrong password"


@router.post("/register", response_model=AuthOut, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest, request: Request, db: AsyncSession = Depends(get_db)
) -> AuthOut:
    if settings.registration_mode == "closed":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "registration closed")
    ip = limits.client_ip(request)
    limits.register_attempts_by_ip.check(ip)
    limits.register_attempts_by_ip.record(ip)
    if settings.registration_mode == "invite" and not _invite_code_ok(body.invite_code):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid invite code")
    try:
        user = await accounts.create_user(db, email=body.email, password=body.password)
    except accounts.EmailTaken as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    log.info("auth.register user_id=%s", user.id)
    return await _session_for(db, user)


@router.post("/login", response_model=AuthOut)
async def login(
    body: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)
) -> AuthOut:
    email = accounts.valid_email(body.email)
    # An invalid address is limited under its plain form; it matches no account.
    limit_key = email if email is not None else body.email.strip().lower()
    ip = limits.client_ip(request)
    limits.login_attempts_by_ip.check(ip)
    # Reserved as a failure before the password check (no await in between),
    # taken back on success: concurrent guesses count while in flight.
    reserved = limits.login_failures_by_email.reserve(limit_key)
    limits.login_attempts_by_ip.record(ip)
    user = await accounts.authenticate(db, email=email, password=body.password)
    if user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, INVALID_LOGIN, headers={"WWW-Authenticate": "Bearer"}
        )
    limits.login_failures_by_email.forgive(limit_key, reserved)
    await AuthTokenRepo(db).delete_expired()
    return await _session_for(db, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    current: CurrentSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Revoke the presented token."""
    await AuthTokenRepo(db).revoke(current.token_hash)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: PasswordChangeRequest,
    current: CurrentSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Set a new password; every other session of the user is signed out."""
    user = await _with_current_password(current.user, body.current_password)
    await accounts.set_password(db, user, body.new_password, keep_token=current.token_hash)


@router.post("/email", response_model=UserOut)
async def change_email(
    body: EmailChangeRequest,
    current: CurrentSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    user = await _with_current_password(current.user, body.current_password)
    try:
        user = await accounts.set_email(db, user, body.new_email)
    except accounts.EmailTaken as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return UserOut(id=user.id, email=user.email)


@router.post("/session/validate", status_code=status.HTTP_204_NO_CONTENT)
async def validate_session(_: User = Depends(get_current_user)) -> None:
    """If the bearer token is live, return 204; otherwise the dep raises 401."""
    return None


def _invite_code_ok(code: str | None) -> bool:
    if code is None:
        return False
    return hmac.compare_digest(code.strip().encode(), settings.invite_code.encode())


async def _with_current_password(user: User, password: str) -> User:
    """`user` when `password` is theirs; 403 otherwise, rate limited per user."""
    key = str(user.id)
    # Reserved before the await, taken back on success (see login).
    reserved = limits.account_failures_by_user.reserve(key)
    if not await passwords.verify_password(user.password_hash, password):
        raise HTTPException(status.HTTP_403_FORBIDDEN, WRONG_PASSWORD)
    limits.account_failures_by_user.forgive(key, reserved)
    return user


async def _session_for(db: AsyncSession, user: User) -> AuthOut:
    token, row = await AuthTokenRepo(db).issue(user.id)
    return AuthOut(
        token=token, expires_at=row.expires_at, user=UserOut(id=user.id, email=user.email)
    )
