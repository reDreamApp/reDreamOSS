"""Auth-feature schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, BaseModel, Field

from ....core.config import INVITE_CODE_MAX_LEN
from ....core.passwords import PASSWORD_MAX_LEN, PASSWORD_MIN_LEN
from ....services.accounts import EMAIL_MAX_LEN, normalize_email

# Checked (at most EMAIL_MAX_LEN characters) and normalized: stripped,
# lower-cased. Invalid → 422.
Email = Annotated[str, AfterValidator(normalize_email)]
# A new password: length rules only. Over-long ones are refused before hashing.
NewPassword = Annotated[str, Field(min_length=PASSWORD_MIN_LEN, max_length=PASSWORD_MAX_LEN)]
# A password to check: only bounded, so a wrong one is a 401/403, not a 422.
Password = Annotated[str, Field(max_length=PASSWORD_MAX_LEN)]


class RegisterRequest(BaseModel):
    email: Email
    password: NewPassword
    invite_code: str | None = Field(default=None, max_length=INVITE_CODE_MAX_LEN)


class LoginRequest(BaseModel):
    # Not validated as an address: an invalid one simply matches no account.
    email: str = Field(max_length=EMAIL_MAX_LEN)
    password: Password


class PasswordChangeRequest(BaseModel):
    current_password: Password
    new_password: NewPassword


class EmailChangeRequest(BaseModel):
    current_password: Password
    new_email: Email


class UserOut(BaseModel):
    id: UUID
    email: str


class AuthOut(BaseModel):
    """A new session. `token` goes in `Authorization: Bearer <token>`."""

    token: str
    expires_at: datetime
    user: UserOut
