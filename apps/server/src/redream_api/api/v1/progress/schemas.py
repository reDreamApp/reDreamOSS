"""Progress schemas."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class DailyMoodOut(BaseModel):
    mood_date: date
    emotion: str


class DailyMoodUpdate(BaseModel):
    emotion: str


class ProgressStatsOut(BaseModel):
    rehearsal_streak: int
    average_intensity: float | None
    average_mood: float | None
    today_mood: str | None
    dreams_logged: int
    nightmares_treated: int
