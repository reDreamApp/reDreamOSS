"""User account management — delete account, GDPR purge, Art. 9 consent."""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.config import settings
from ....core.dependencies import get_current_user, get_db
from ....db.models.user import User
from ....db.repositories.consent_repo import ConsentRepo
from ....db.repositories.user_repo import UserRepo
from ....services.auth_service import delete_login
from ....services.rehearsal_audio import AUDIO_NOT_DELETED, delete_user_audio

log = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["users"])

# Both languages: builds without text_version do not tell their language.
# No umlauts, so the text survives any decoding on old builds.
_UPDATE_APP_MESSAGE = (
    "Please install the latest version of the app to continue.\n"
    "Bitte installiere die neueste Version der App, um fortzufahren."
)


class ConsentStatusOut(BaseModel):
    required_version: str
    granted_version: str | None
    consent_required: bool
    """True when the app must show the consent screen before any health-data
    feature: no grant yet, withdrawn, or the consent text changed."""


class ConsentGrantRequest(BaseModel):
    text_version: str | None = None
    """Version bundled in the client, rather than echoed from server status."""
    version: str
    """The consent-text version the user actually saw — must match the
    current required version (a stale screen must not record a grant)."""


@router.get("/me/consent", response_model=ConsentStatusOut)
async def get_consent(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConsentStatusOut:
    latest = await ConsentRepo(db).latest(user.id)
    granted_version = latest.version if latest is not None and latest.granted else None
    return ConsentStatusOut(
        required_version=settings.consent_version,
        granted_version=granted_version,
        consent_required=granted_version != settings.consent_version,
    )


@router.post("/me/consent", status_code=status.HTTP_204_NO_CONTENT)
async def grant_consent(
    body: ConsentGrantRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Records explicit consent (Art. 9(2)(a)) as an append-only proof row."""
    if body.text_version is None:
        # Builds from before 2026-10-07 send no text_version. They reload the
        # form silently on a 409 but show any other error's detail under it.
        raise HTTPException(status.HTTP_426_UPGRADE_REQUIRED, _UPDATE_APP_MESSAGE)
    if body.version != settings.consent_version or body.text_version != settings.consent_version:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "consent text changed — update the app and reload the consent screen",
        )
    await ConsentRepo(db).record(user.id, version=body.version, granted=True)


@router.delete("/me/consent", status_code=status.HTTP_204_NO_CONTENT)
async def withdraw_consent(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Withdrawal — as easy as granting (Art. 7(3)). Data stays until the
    user deletes it; health-data features gate until re-consent."""
    await ConsentRepo(db).record(
        user.id, version=settings.consent_version, granted=False
    )


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_me(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete the account: rehearsal audio, app data, then the login.

    The audio folder goes first: if that fails the answer is 503 and nothing
    else is deleted. Deleting the `users` row cascades to dreams, chats and
    the rest. The Supabase login goes last: if that fails the answer is 503
    and the user is left with an empty account they can sign in to and
    delete again.
    """
    user_id = user.id
    await _delete_audio(user_id)
    await UserRepo(db).delete(user_id)
    if not await delete_login(user_id):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "the login could not be deleted — try again",
        )


@router.delete("/me/data", status_code=status.HTTP_204_NO_CONTENT)
async def delete_my_data(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """GDPR purge: delete this user's rehearsal audio, then their Postgres data.

    The login stays, so the user can keep using the app with an empty account.

    The audio folder goes first: if that fails the answer is 503 and the
    database is left untouched, so the user can try again.
    """
    user_id = user.id
    await _delete_audio(user_id)
    await UserRepo(db).delete(user_id)


async def _delete_audio(user_id: UUID) -> None:
    """Delete the user's audio folder; 503 when that fails."""
    try:
        await delete_user_audio(user_id)
    except (OSError, ValueError) as exc:
        log.error("rehearsal audio could not be deleted (%s)", type(exc).__name__)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, AUDIO_NOT_DELETED) from exc
