"""Dreams CRUD + favorite + practice + rehearsal."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from ....core.dependencies import get_current_user, get_db, require_consent
from ....db.models.dream import Dream
from ....db.models.user import User
from ....db.repositories.dream_repo import DreamRepo
from ....services.ai_client import (
    VOICE_UNAVAILABLE,
    AIServiceError,
    BaseAiClient,
    get_ai_client,
)
from ....services.audio_links import link_for
from ....services.rehearsal_audio import (
    AUDIO_NOT_DELETED,
    AUDIO_NOT_READ,
    audio_epoch,
    audio_exists,
    clear_rehearsal,
    delete_dream_audio,
    discard_dream_audio,
    drop_if_stale,
    owns_key,
)
from .schemas import DreamCreate, DreamOut, DreamUpdate, RehearsalGenerateRequest

log = logging.getLogger(__name__)

router = APIRouter(prefix="/dreams", tags=["dreams"])


def _to_out(dream: Dream) -> DreamOut:
    return DreamOut(
        id=dream.id,
        title=dream.title,
        original_dream=dream.original_dream,
        rewritten_dream=dream.rewritten_dream,
        intensity=dream.intensity,
        is_nightmare=dream.is_nightmare,
        created_at=dream.created_at,
        last_edited=dream.last_edited,
        practice_timestamps=list(dream.practice_timestamps or []),
        is_current_dream=dream.is_current_dream,
        image_url=dream.image_url,
        session_id=dream.session_id,
        source=dream.source,
        # A fresh short-lived link on every answer; the row keeps the key.
        rehearsal_audio_url=link_for(dream.rehearsal_audio_key),
        rehearsal_script=dream.rehearsal_script,
        rehearsal_duration=dream.rehearsal_duration,
        rehearsal_language=dream.rehearsal_language,
        rehearsal_generated_at=dream.rehearsal_generated_at,
    )


@router.get("", response_model=list[DreamOut])
async def list_dreams(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[DreamOut]:
    rows = await DreamRepo(db).list_for_user(user.id)
    return [_to_out(d) for d in rows]


@router.get("/{dream_id}", response_model=DreamOut)
async def get_dream(
    dream_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DreamOut:
    dream = await DreamRepo(db).get_for_user(user.id, dream_id)
    if dream is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "dream not found")
    return _to_out(dream)


@router.post(
    "",
    response_model=DreamOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_consent)],
)
async def create_dream(
    body: DreamCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DreamOut:
    dream = Dream(user_id=user.id, **body.model_dump())
    created = await DreamRepo(db).create(dream)
    return _to_out(created)


@router.put(
    "/{dream_id}",
    response_model=DreamOut,
    dependencies=[Depends(require_consent)],
)
async def update_dream(
    dream_id: int,
    body: DreamUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DreamOut:
    repo = DreamRepo(db)
    dream = await repo.get_for_user(user.id, dream_id)
    if dream is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "dream not found")

    rewritten_changed = (
        body.rewritten_dream is not None and body.rewritten_dream != dream.rewritten_dream
    )

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(dream, field, value)
    dream.last_edited = datetime.now(UTC)

    # If the rewritten dream changed, the cached rehearsal audio is stale.
    if rewritten_changed:
        await discard_dream_audio(user.id, dream.id)
        clear_rehearsal(dream)

    saved = await repo.save(dream)
    return _to_out(saved)


@router.delete("/{dream_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dream(
    dream_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    repo = DreamRepo(db)
    dream = await repo.get_for_user(user.id, dream_id)
    if dream is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "dream not found")
    # Audio first: a failure keeps the row, so the user can delete again.
    try:
        await delete_dream_audio(user.id, dream.id)
    except (OSError, ValueError) as exc:
        log.error("rehearsal audio could not be deleted (%s)", type(exc).__name__)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, AUDIO_NOT_DELETED) from exc
    await repo.delete(dream)


@router.patch("/{dream_id}/favorite", response_model=DreamOut)
async def set_current_dream(
    dream_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DreamOut:
    repo = DreamRepo(db)
    dream = await repo.get_for_user(user.id, dream_id)
    if dream is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "dream not found")
    await repo.clear_current_for_user(user.id)
    dream.is_current_dream = True
    saved = await repo.save(dream)
    return _to_out(saved)


@router.post("/{dream_id}/practice", response_model=DreamOut)
async def record_practice(
    dream_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DreamOut:
    repo = DreamRepo(db)
    dream = await repo.get_for_user(user.id, dream_id)
    if dream is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "dream not found")

    timestamps = list(dream.practice_timestamps or [])
    timestamps.append(datetime.now(UTC).isoformat())
    dream.practice_timestamps = timestamps
    saved = await repo.save(dream)
    return _to_out(saved)


@router.post(
    "/{dream_id}/rehearsal", response_model=DreamOut, dependencies=[Depends(require_consent)]
)
async def generate_rehearsal(
    dream_id: int,
    body: RehearsalGenerateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    ai: BaseAiClient = Depends(get_ai_client),
) -> DreamOut:
    repo = DreamRepo(db)
    dream = await repo.get_for_user(user.id, dream_id)
    if dream is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "dream not found")
    if not dream.rewritten_dream or not dream.rewritten_dream.strip():
        raise HTTPException(status.HTTP_409_CONFLICT, "rewritten_dream is required for rehearsal")

    # Cache: if audio already exists and matches the requested language, reuse.
    # This holds while speech output is off too: it signs a fresh link to the
    # stored file (as GET /rehearsal-url does) and sends nothing to TTS.
    if (
        dream.rehearsal_audio_key
        and dream.rehearsal_language == body.language
        and dream.rehearsal_generated_at is not None
    ):
        if await _has_audio(dream):
            return _to_out(dream)
        # The file is gone: forget it, then render it anew (TTS permitting).
        clear_rehearsal(dream)
        await repo.save(dream)

    # New audio needs TTS; switched off (no TTS_MODEL or voice), say so
    # before any AI call.
    if not ai.tts_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, VOICE_UNAVAILABLE)

    # Rendering takes long; the dream or account may be deleted (or the text
    # edited) meanwhile. Then the file just written must go too.
    started = audio_epoch(user.id, dream.id)
    try:
        result = await ai.rehearsal_generate(
            {
                "dream_id": str(dream.id),
                "user_id": str(user.id),
                "rewritten_dream": dream.rewritten_dream,
                "language": body.language,
            }
        )
    except AIServiceError as exc:
        await drop_if_stale(user.id, dream.id, started)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    if await drop_if_stale(user.id, dream.id, started):
        raise HTTPException(status.HTTP_409_CONFLICT, "the dream changed meanwhile, try again")

    key = result.get("audio_key")
    if not owns_key(key, user.id, dream.id):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "rehearsal audio unavailable")
    dream.rehearsal_audio_key = key
    dream.rehearsal_script = result.get("script")
    dream.rehearsal_duration = result.get("duration_estimate")
    dream.rehearsal_language = body.language
    dream.rehearsal_generated_at = datetime.now(UTC)
    try:
        saved = await repo.save(dream)
    except StaleDataError as exc:  # deleted after the check; its folder went with it
        raise HTTPException(status.HTTP_404_NOT_FOUND, "dream not found") from exc
    return _to_out(saved)


async def _has_audio(dream: Dream) -> bool:
    """audio_exists, with a file that cannot be checked as 503: the stored
    key is kept rather than cleared on a passing disk error."""
    try:
        return await audio_exists(dream)
    except OSError as exc:
        log.error("rehearsal audio could not be read (%s)", type(exc).__name__)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, AUDIO_NOT_READ) from exc


@router.get("/{dream_id}/rehearsal-url", response_model=DreamOut)
async def refresh_audio(
    dream_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> DreamOut:
    """The dream with a fresh link to its stored audio; 404 without one."""
    repo = DreamRepo(db)
    dream = await repo.get_for_user(user.id, dream_id)
    if dream is None:
        raise HTTPException(404, "dream not found")
    if dream.rehearsal_audio_key and await _has_audio(dream):
        return _to_out(dream)
    if clear_rehearsal(dream):
        await repo.save(dream)
    raise HTTPException(404, "no rehearsal audio")
