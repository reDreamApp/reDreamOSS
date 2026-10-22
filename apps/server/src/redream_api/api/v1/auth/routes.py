"""Auth-related routes.

Supabase manages the actual auth flow (sign-in / sign-up / password reset).
These routes are thin: validate that an inbound JWT is real (via
`get_current_user`), and trigger Supabase's password-reset email.
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends, status

from ....core.config import settings
from ....core.dependencies import get_current_user
from ....db.models.user import User
from .schemas import PasswordResetRequest, PasswordResetResponse

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/session/validate", status_code=status.HTTP_204_NO_CONTENT)
async def validate_session(_: User = Depends(get_current_user)) -> None:
    """If the bearer token verifies, return 204; otherwise the dep raises 401."""
    return None


@router.post("/password-reset", response_model=PasswordResetResponse)
async def password_reset(req: PasswordResetRequest) -> PasswordResetResponse:
    """Trigger a Supabase password-reset email.

    Always returns `account_exists=True` to avoid email-enumeration. `email_sent`
    reflects whether Supabase accepted the request.
    """

    if not settings.supabase_url or not settings.supabase_service_role_key:
        log.warning("auth.password_reset.skipped reason=supabase_not_configured")
        return PasswordResetResponse(account_exists=True, email_sent=False)

    url = f"{settings.supabase_url.rstrip('/')}/auth/v1/recover"
    headers = {
        "apikey": settings.supabase_service_role_key,
        "Authorization": f"Bearer {settings.supabase_service_role_key}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(url, headers=headers, json={"email": str(req.email)})
        sent = r.status_code < 300
    except Exception:  # noqa: BLE001 — never leak Supabase internals
        log.exception("auth.password_reset.failed")
        sent = False

    return PasswordResetResponse(account_exists=True, email_sent=sent)
