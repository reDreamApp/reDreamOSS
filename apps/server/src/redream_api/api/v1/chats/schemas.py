"""Chats schemas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from ....core.request_model import RequestModel


class ChatStartRequest(RequestModel):
    language: str = Field(default="en", pattern="^(en|de)$")


class ChatStartResponse(BaseModel):
    chat_id: int
    language: str
    current_stage: str


class ChatOut(BaseModel):
    id: int
    language: str
    current_stage: str
    is_done: bool
    is_nightmare: bool | None
    title: str | None
    created_at: datetime
    updated_at: datetime


class ChatMessageOut(BaseModel):
    id: UUID
    role: str
    content: str
    stage: str | None
    created_at: datetime


class ChatMessageRequest(RequestModel):
    content: str = Field(min_length=1, max_length=10_000)


class ChatIntensityRequest(RequestModel):
    intensity: int = Field(ge=0, le=10)


class ChatIntensityResponse(BaseModel):
    chat_id: int
    dream_id: int
    intensity: int


class SafetyEventPayload(BaseModel):
    risk: str
    categories: list[str]
    confidence: float
    message: str | None = None
    """Localized helpline text for the client to render in place of the AI
    reply (ADR 0005) — without it a high-risk turn has nothing to show."""


class ChatMessageResponse(BaseModel):
    chat_id: int
    user_message: ChatMessageOut
    ai_message: ChatMessageOut | None = None
    stage: str
    is_done: bool
    safety_event: SafetyEventPayload | None = None
    """Present (non-null) when the safety classifier returned risk=high. The IRT
    state did NOT advance and `ai_message` is null. Frontend renders the
    helpline overlay (ADR 0005)."""
