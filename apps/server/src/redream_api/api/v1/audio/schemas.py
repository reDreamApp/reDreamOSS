"""Audio schemas."""

from __future__ import annotations

from pydantic import BaseModel


class TranscribeOut(BaseModel):
    text: str
    language_detected: str | None = None
    """Best-effort provider detection (en/de) — None in practice for the
    current providers' default response mode. The client already knows the
    chat language; don't build logic on this field."""
