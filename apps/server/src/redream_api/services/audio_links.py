"""Short-lived signed links to rehearsal MP3s (served by GET /audio/files/{key}).

Link: ``/api/v1/audio/files/{key}?exp={unix_seconds}&sig={hex}``, relative to
the server; ``sig`` is HMAC-SHA256 over ``{key}:{exp}``. Audio players send no
auth header, so the signature is the authorization: it names one file until
``exp`` (REHEARSAL_URL_TTL_SECONDS from now).

The secret is generated per process at import and never stored: links die
with a restart (the app then asks for a fresh one), and every worker would
have its own — one uvicorn worker is required.

redream_ai's store (the key format) is imported on first use, as everywhere
in the backend.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time

from ..core.config import settings

# Where the v1 router (main.py) mounts the audio router's file route.
FILES_PATH = "/api/v1/audio/files/"

# Leeway for a clock that moves between signing and checking.
_SKEW_SECONDS = 60

# Canonical decimal only (no sign, no leading zero): the signature covers
# int(exp), so one exp has exactly one spelling.
_EXP_RE = re.compile(r"^[1-9][0-9]{0,11}$")
_SIG_RE = re.compile(r"^[0-9a-f]{64}$")

# Test seam: tests may replace it; nothing else reads it.
_secret = secrets.token_bytes(32)


def sign(key: str, *, now: float | None = None) -> str:
    """A link to `key`'s file, valid for REHEARSAL_URL_TTL_SECONDS."""
    from redream_ai.audio import store

    if not store.is_key(key):
        raise ValueError("invalid audio key")
    exp = int(now if now is not None else time.time()) + settings.rehearsal_url_ttl_seconds
    return f"{FILES_PATH}{key}?exp={exp}&sig={_signature(key, exp)}"


def link_for(key: str | None) -> str | None:
    """A fresh link to a dream's stored key; None without one (or for a value
    that is not a key)."""
    from redream_ai.audio import store

    return sign(key) if store.is_key(key) else None


def verify(key: str, exp: str, sig: str, *, now: float | None = None) -> bool:
    """True when `sig` signs `key` until `exp` and `exp` has not passed (nor
    lies further ahead than any link this process hands out)."""
    from redream_ai.audio import store

    if not store.is_key(key) or not _EXP_RE.fullmatch(exp) or not _SIG_RE.fullmatch(sig):
        return False
    expires = int(exp)
    current = int(now if now is not None else time.time())
    if expires < current or expires > current + settings.rehearsal_url_ttl_seconds + _SKEW_SECONDS:
        return False
    return hmac.compare_digest(_signature(key, expires), sig)


def _signature(key: str, exp: int) -> str:
    return hmac.new(_secret, f"{key}:{exp}".encode(), hashlib.sha256).hexdigest()
