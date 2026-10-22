"""Supabase JWT verification — fetch JWKS, verify signature + claims."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from cachetools import TTLCache
from jose import jwt
from jose.exceptions import JWTError

from .config import settings

log = logging.getLogger(__name__)

# JWKS cache: 1h TTL is plenty — Supabase rotates keys infrequently.
_jwks_cache: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=4, ttl=3600)


class JwtVerificationError(Exception):
    """Raised when the bearer token fails verification."""


async def _fetch_jwks() -> dict[str, Any]:
    cached = _jwks_cache.get(settings.supabase_jwks_url)
    if cached is not None:
        return cached

    if not settings.supabase_jwks_url:
        raise JwtVerificationError("SUPABASE_JWKS_URL is not configured")

    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(settings.supabase_jwks_url)
        r.raise_for_status()
        jwks = r.json()
    _jwks_cache[settings.supabase_jwks_url] = jwks
    return jwks


def _key_for_kid(jwks: dict[str, Any], kid: str | None) -> dict[str, Any] | None:
    if kid is None:
        return None
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            return key
    return None


async def verify_supabase_jwt(token: str) -> dict[str, Any]:
    """Verify `token` against the configured JWKS. Returns the decoded claims dict."""

    try:
        unverified_header = jwt.get_unverified_header(token)
    except JWTError as exc:
        raise JwtVerificationError(f"malformed token: {exc}") from exc

    alg = unverified_header.get("alg")
    if alg not in settings.jwt_algorithms_list:
        raise JwtVerificationError(f"unexpected algorithm: {alg}")

    kid = unverified_header.get("kid")
    jwks = await _fetch_jwks()
    key = _key_for_kid(jwks, kid)
    if key is None:
        raise JwtVerificationError(f"no JWKS key for kid={kid}")

    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=settings.jwt_algorithms_list,
            audience=settings.supabase_jwt_aud,
            issuer=settings.supabase_jwt_iss or None,
        )
    except JWTError as exc:
        raise JwtVerificationError(f"verification failed: {exc}") from exc

    return claims
