"""Password hashing: argon2id with argon2-cffi's default parameters.

Each hash or verification takes ~64 MiB and tens of milliseconds of CPU, so it
runs in a worker thread (the event loop keeps serving) and at most
`_CONCURRENCY` run at once: a burst of logins waits instead of exhausting the
box's memory. Callers check the rate limits (core/limits) before calling in.
"""

from __future__ import annotations

import asyncio

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

PASSWORD_MIN_LEN = 8
# Longer ones are refused before hashing: argon2 reads the whole input.
PASSWORD_MAX_LEN = 1024

_CONCURRENCY = 2

_hasher = PasswordHasher()
_slots = asyncio.Semaphore(_CONCURRENCY)

# Verified against when the email is unknown, so a login takes as long
# whether or not the account exists. Made at startup (main.lifespan), with the
# same parameters as real hashes, so no login pays for making it.
_dummy_hash: str | None = None


def password_problem(password: str) -> str | None:
    """Why `password` is not allowed, None when it is. Length only: no
    composition rules."""
    if len(password) < PASSWORD_MIN_LEN:
        return f"password must be at least {PASSWORD_MIN_LEN} characters"
    if len(password) > PASSWORD_MAX_LEN:
        return f"password must be at most {PASSWORD_MAX_LEN} characters"
    return None


async def hash_password(password: str) -> str:
    async with _slots:
        return await asyncio.to_thread(_hasher.hash, password)


async def verify_password(password_hash: str, password: str) -> bool:
    """True when `password` matches `password_hash`; False for a mismatch or a
    hash that cannot be read."""
    async with _slots:
        return await asyncio.to_thread(_verify, password_hash, password)


async def prepare_dummy_hash() -> str:
    """The dummy hash, made now unless it exists. Without a startup (tests,
    no lifespan) the first unknown-email login makes it."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = await hash_password("not the password of any account")
    return _dummy_hash


async def verify_dummy(password: str) -> None:
    """Spend the time of a verification for an account that does not exist."""
    await verify_password(await prepare_dummy_hash(), password)


def needs_rehash(password_hash: str) -> bool:
    """True when `password_hash` was made with other parameters than today's."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def _verify(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):  # a mismatch is a VerificationError
        return False
