"""The base of every JSON request body: no NUL character in any text.

Postgres text cannot hold U+0000; a body with one would fail at its first
write with a 500. Refused here with a 422 instead, for every field at once.
"""

from __future__ import annotations

from typing import Any, Self

from pydantic import BaseModel, model_validator


def _has_nul(value: Any) -> bool:
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, BaseModel):
        return _has_nul(value.__dict__)
    if isinstance(value, dict):
        return any(_has_nul(k) or _has_nul(v) for k, v in value.items())
    if isinstance(value, list | tuple | set | frozenset):
        return any(_has_nul(item) for item in value)
    return False


class RequestModel(BaseModel):
    @model_validator(mode="after")
    def _no_nul_characters(self) -> Self:
        if _has_nul(self.__dict__):
            raise ValueError("text must not contain NUL characters")
        return self
