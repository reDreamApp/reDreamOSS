"""Profile schemas."""

from __future__ import annotations

from pydantic import BaseModel


class ProfileOut(BaseModel):
    gender: str | None = None
    age_range: str | None = None
    nightmare_frequency: str | None = None


class ProfileUpdate(BaseModel):
    gender: str | None = None
    age_range: str | None = None
    nightmare_frequency: str | None = None


class NightmareFrequencyUpdate(BaseModel):
    nightmare_frequency: str
