"""Supabase Auth admin call for account deletion.

The login (email, password hash, sessions) lives in Supabase's `auth.users`,
outside the tables redream_api owns. It is deleted through the Auth admin API
with the service-role key:

- ``DELETE /auth/v1/admin/users/{user_id}``

Unlike the Storage purge this one reports failure: a login left
behind still signs in, so the route has to tell the user to try again.
"""

from __future__ import annotations

import logging
from uuid import UUID

import httpx

from ..core.config import settings

log = logging.getLogger(__name__)

_TIMEOUT = 30.0

# Test seam: tests install an httpx.MockTransport here; None means real HTTP.
_transport: httpx.AsyncBaseTransport | None = None


async def delete_login(user_id: UUID) -> bool:
    """Delete the user's Supabase login. Returns False if it may still exist."""
    if not settings.supabase_url or not settings.supabase_service_role_key:
        log.error("auth.delete.failed reason=supabase_not_configured user_id=%s", user_id)
        return False
    key = settings.supabase_service_role_key
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, transport=_transport) as client:
            r = await client.delete(
                f"{settings.supabase_url.rstrip('/')}/auth/v1/admin/users/{user_id}",
                headers={"Authorization": f"Bearer {key}", "apikey": key},
            )
    except httpx.HTTPError as exc:
        log.error("auth.delete.failed user_id=%s error=%s", user_id, exc)
        return False
    # 404: already gone, e.g. the retry after a deletion that stopped half way.
    if r.status_code >= 300 and r.status_code != 404:
        log.error("auth.delete.failed user_id=%s status=%d", user_id, r.status_code)
        return False
    log.info("auth.delete.ok user_id=%s", user_id)
    return True
