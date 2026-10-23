"""Accounts: email rules, creating users, checking and setting passwords.

Shared by the auth routes and the operator's CLI (redream_api.cli), so both
apply the same rules. Rate limits are the routes' business (core/limits).
"""

from __future__ import annotations

from email_validator import EmailNotValidError, validate_email
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core import passwords
from ..db.models.user import User
from ..db.repositories.auth_token_repo import AuthTokenRepo
from ..db.repositories.user_repo import UserRepo

EMAIL_MAX_LEN = 254


class AccountError(Exception):
    """A request the account rules refuse; the message is safe to show."""


class EmailTaken(AccountError):
    def __init__(self) -> None:
        super().__init__("email already registered")


def normalize_email(raw: str) -> str:
    """`raw` checked and normalized: stripped, lower-cased. ValueError when it
    is no valid address (syntax only, no DNS lookup)."""
    email = raw.strip()
    if not email or len(email) > EMAIL_MAX_LEN:
        raise ValueError("invalid email")
    try:
        normalized = validate_email(email, check_deliverability=False).normalized
    except EmailNotValidError as exc:
        raise ValueError("invalid email") from exc
    return normalized.lower()


def valid_email(raw: str) -> str | None:
    """A login's email normalized, None when it is no valid address (it then
    matches no account and is not looked up)."""
    try:
        return normalize_email(raw)
    except ValueError:
        return None


async def create_user(db: AsyncSession, *, email: str, password: str) -> User:
    """A new account. `email` must be normalized. Raises EmailTaken, or
    AccountError for a password the rules refuse."""
    _check_password(password)
    repo = UserRepo(db)
    # Checked first so a duplicate costs no hash; the unique index decides.
    if await repo.get_by_email(email) is not None:
        raise EmailTaken()
    password_hash = await passwords.hash_password(password)
    try:
        return await repo.create(email=email, password_hash=password_hash)
    except IntegrityError as exc:
        await db.rollback()
        raise EmailTaken() from exc


async def authenticate(db: AsyncSession, *, email: str | None, password: str) -> User | None:
    """The account for `email` (normalized; None for an invalid address) when
    `password` is right, else None. Unknown and invalid emails take as long as
    known ones. Rehashes a password made with older parameters."""
    user = await UserRepo(db).get_by_email(email) if email is not None else None
    if user is None:
        await passwords.verify_dummy(password)
        return None
    if not await passwords.verify_password(user.password_hash, password):
        return None
    if passwords.needs_rehash(user.password_hash):
        user.password_hash = await passwords.hash_password(password)
        await UserRepo(db).save(user)
    return user


async def set_password(
    db: AsyncSession, user: User, password: str, *, keep_token: str | None = None
) -> None:
    """Set `user`'s password and revoke their tokens, except `keep_token` (the
    hash of the session that made the change). Raises AccountError for a
    password the rules refuse."""
    _check_password(password)
    user.password_hash = await passwords.hash_password(password)
    await UserRepo(db).save(user)
    await AuthTokenRepo(db).revoke_all(user.id, keep=keep_token)


async def set_email(db: AsyncSession, user: User, email: str) -> User:
    """Change `user`'s login email (`email` normalized). Raises EmailTaken."""
    if email == user.email:
        return user
    repo = UserRepo(db)
    if await repo.get_by_email(email) is not None:
        raise EmailTaken()
    user.email = email
    try:
        return await repo.save(user)
    except IntegrityError as exc:
        await db.rollback()
        raise EmailTaken() from exc


def _check_password(password: str) -> None:
    problem = passwords.password_problem(password)
    if problem is not None:
        raise AccountError(problem)
