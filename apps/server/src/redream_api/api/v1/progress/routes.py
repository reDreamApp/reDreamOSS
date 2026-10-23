"""Progress: rehearsal streak, intensity avg, mood avg, today's mood."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from itertools import takewhile

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.dependencies import get_current_user, get_db
from ....db.models.user import User
from ....db.repositories.daily_mood_repo import DailyMoodRepo
from ....db.repositories.dream_repo import DreamRepo
from .schemas import DailyMoodUpdate, ProgressStatsOut

router = APIRouter(prefix="/progress", tags=["progress"])

_MOOD_TO_SCORE = {
    "Very Dissatisfied": 1.0,
    "Dissatisfied": 2.0,
    "Neutral": 3.0,
    "Satisfied": 4.0,
    "Very Satisfied": 5.0,
}


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


@router.get("/stats", response_model=ProgressStatsOut)
async def get_stats(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProgressStatsOut:
    dreams = await DreamRepo(db).list_for_user(user.id, limit=1000)
    nightmares = [d for d in dreams if d.is_nightmare]

    intensities = [d.intensity for d in nightmares if d.intensity is not None]
    avg_intensity = (sum(intensities) / len(intensities)) if intensities else None

    today = _today_utc()
    moods = await DailyMoodRepo(db).list_for_user_between(
        user.id, start=today - timedelta(days=29), end=today
    )
    mood_scores = [_MOOD_TO_SCORE.get(m.emotion) for m in moods]
    mood_scores = [s for s in mood_scores if s is not None]
    avg_mood = (sum(mood_scores) / len(mood_scores)) if mood_scores else None

    today_mood_row = next((m for m in moods if m.mood_date == today), None)
    today_mood = today_mood_row.emotion if today_mood_row else None

    streak = _rehearsal_streak(dreams)

    return ProgressStatsOut(
        rehearsal_streak=streak,
        average_intensity=avg_intensity,
        average_mood=avg_mood,
        today_mood=today_mood,
        dreams_logged=len(dreams),
        dreams_rewritten=sum(1 for d in dreams if d.rewritten_dream),
    )


def _rehearsal_streak(dreams: list) -> int:
    """Count consecutive days, ending today, with at least one practice timestamp."""
    days_with_practice: set[date] = set()
    for d in dreams:
        for ts in d.practice_timestamps or []:
            try:
                day = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).date()
            except ValueError:
                continue
            days_with_practice.add(day)
    today = _today_utc()
    return sum(
        1
        for _ in takewhile(
            lambda d: d in days_with_practice,
            (today - timedelta(days=i) for i in range(0, 365)),
        )
    )


@router.post("/emotion", status_code=204)
async def upsert_today_mood(
    body: DailyMoodUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await DailyMoodRepo(db).upsert_for_user_date(
        user_id=user.id, mood_date=_today_utc(), emotion=body.emotion
    )
