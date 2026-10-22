"""Data export — single JSON dump of the user's profile + chats + dreams."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.dependencies import get_current_user, get_db
from ....db.models.user import User
from ....db.repositories.chat_message_repo import ChatMessageRepo
from ....db.repositories.chat_repo import ChatRepo
from ....db.repositories.dream_repo import DreamRepo

router = APIRouter(prefix="/exports", tags=["exports"])


@router.get("/data")
async def export_data(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    chats = await ChatRepo(db).list_for_user(user.id, limit=10_000)
    dreams = await DreamRepo(db).list_for_user(user.id, limit=10_000)
    msg_repo = ChatMessageRepo(db)

    chats_payload: list[dict[str, Any]] = []
    for c in chats:
        msgs = await msg_repo.list_for_chat(c.id, limit=10_000)
        chats_payload.append(
            {
                "id": c.id,
                "language": c.language,
                "current_stage": c.current_stage,
                "is_done": c.is_done,
                "created_at": c.created_at.isoformat(),
                "messages": [
                    {
                        "id": str(m.id),
                        "role": m.role,
                        "stage": m.stage,
                        "content": m.content,
                        "created_at": m.created_at.isoformat(),
                    }
                    for m in msgs
                ],
            }
        )

    return {
        "user": {
            "id": str(user.id),
            "email": user.email,
            "gender": user.gender,
            "age_range": user.age_range,
            "nightmare_frequency": user.nightmare_frequency,
            "created_at": user.created_at.isoformat(),
        },
        "dreams": [
            {
                "id": d.id,
                "title": d.title,
                "original_dream": d.original_dream,
                "rewritten_dream": d.rewritten_dream,
                "intensity": d.intensity,
                "is_nightmare": d.is_nightmare,
                "source": d.source,
                "created_at": d.created_at.isoformat(),
                "practice_timestamps": list(d.practice_timestamps or []),
            }
            for d in dreams
        ],
        "chats": chats_payload,
    }
