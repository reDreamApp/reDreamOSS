"""Dreams CRUD + favorite + practice + rehearsal."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

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
from ....services.storage_service import delete_rehearsal_audio_for_dream, refresh_rehearsal_url
from .schemas import DreamCreate, DreamOut, DreamUpdate, RehearsalGenerateRequest

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
        rehearsal_audio_url=dream.rehearsal_audio_url,
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
        dream.rehearsal_audio_url = None
        dream.rehearsal_script = None
        dream.rehearsal_duration = None
        dream.rehearsal_language = None
        dream.rehearsal_generated_at = None

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
    await repo.delete(dream)
    await delete_rehearsal_audio_for_dream(user.id, dream_id)


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
    # This holds while speech output is off too: it re-signs stored audio
    # (as GET /rehearsal-url does) and sends nothing to a TTS provider.
    if (
        dream.rehearsal_audio_url
        and dream.rehearsal_language == body.language
        and dream.rehearsal_generated_at is not None
    ):
        return await _refresh_audio(db, dream)

    # New audio needs TTS; switched off (no TTS_MODEL or voice), say so
    # before any AI call.
    if not ai.tts_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, VOICE_UNAVAILABLE)

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
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    dream.rehearsal_audio_url = result.get("audio_url")
    dream.rehearsal_script = result.get("script")
    dream.rehearsal_duration = result.get("duration_estimate")
    dream.rehearsal_language = body.language
    dream.rehearsal_generated_at = datetime.now(UTC)
    saved = await repo.save(dream)
    return _to_out(saved)


async def _refresh_audio(db: AsyncSession, dream: Dream) -> DreamOut:
    if not dream.rehearsal_audio_url:
        raise HTTPException(404, "no rehearsal audio")
    try:
        dream.rehearsal_audio_url = await refresh_rehearsal_url(
            dream.rehearsal_audio_url, dream.user_id, dream.id
        )
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, "rehearsal audio unavailable") from exc
    await db.commit()
    return _to_out(dream)


@router.get("/{dream_id}/rehearsal-url", response_model=DreamOut)
async def refresh_audio(
    dream_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> DreamOut:
    dream = await DreamRepo(db).get_for_user(user.id, dream_id)
    if dream is None:
        raise HTTPException(404, "dream not found")
    return await _refresh_audio(db, dream)
