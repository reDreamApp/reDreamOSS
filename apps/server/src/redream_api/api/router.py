"""Top-level router aggregator. Mounted by `main.py`."""

from __future__ import annotations

from fastapi import APIRouter

from .v1 import (
    audio,
    auth,
    chats,
    dreams,
    exports,
    health,
    instance,
    profile,
    progress,
    users,
)

router_v1 = APIRouter()
router_v1.include_router(health.router)
router_v1.include_router(instance.router)
router_v1.include_router(auth.router)
router_v1.include_router(users.router)
router_v1.include_router(profile.router)
router_v1.include_router(dreams.router)
router_v1.include_router(chats.router)
router_v1.include_router(audio.router)
router_v1.include_router(progress.router)
router_v1.include_router(exports.router)
