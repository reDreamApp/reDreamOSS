"""Auth-feature schemas."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetResponse(BaseModel):
    account_exists: bool
    email_sent: bool
