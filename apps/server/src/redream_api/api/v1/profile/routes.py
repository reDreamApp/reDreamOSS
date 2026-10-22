"""Profile routes — gender / age_range / nightmare_frequency."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.dependencies import get_current_user, get_db
from ....db.models.user import User
from .schemas import NightmareFrequencyUpdate, ProfileOut, ProfileUpdate

router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("", response_model=ProfileOut)
async def read_profile(user: User = Depends(get_current_user)) -> ProfileOut:
    return ProfileOut(
        gender=user.gender,
        age_range=user.age_range,
        nightmare_frequency=user.nightmare_frequency,
    )


@router.post("", response_model=ProfileOut)
async def upsert_profile(
    body: ProfileUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProfileOut:
    if body.gender is not None:
        user.gender = body.gender
    if body.age_range is not None:
        user.age_range = body.age_range
    if body.nightmare_frequency is not None:
        user.nightmare_frequency = body.nightmare_frequency
    await db.commit()
    await db.refresh(user)
    return ProfileOut(
        gender=user.gender,
        age_range=user.age_range,
        nightmare_frequency=user.nightmare_frequency,
    )


@router.patch("/nightmare-frequency", response_model=ProfileOut)
async def patch_nightmare_frequency(
    body: NightmareFrequencyUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProfileOut:
    user.nightmare_frequency = body.nightmare_frequency
    await db.commit()
    await db.refresh(user)
    return ProfileOut(
        gender=user.gender,
        age_range=user.age_range,
        nightmare_frequency=user.nightmare_frequency,
    )
