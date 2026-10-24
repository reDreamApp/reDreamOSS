"""Profile schemas."""

from __future__ import annotations

from pydantic import BaseModel

from ....core.request_model import RequestModel


class ProfileOut(BaseModel):
    gender: str | None = None
    age_range: str | None = None
    nightmare_frequency: str | None = None


class ProfileUpdate(RequestModel):
    gender: str | None = None
    age_range: str | None = None
    nightmare_frequency: str | None = None


class NightmareFrequencyUpdate(RequestModel):
    nightmare_frequency: str
