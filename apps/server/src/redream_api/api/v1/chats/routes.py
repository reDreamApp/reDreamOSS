"""Chat routes — IRT-driven conversation with a safety gate.

Flow for `POST /chats/{id}/message`:
  1. Count the turn against DAILY_AI_REQUESTS_PER_USER (429 over it).
  2. Persist user message.
  2b. Call AI `language_check`: a message written in (or asking for) the
     other supported language switches `chat.language` from this turn on.
  3. Call AI `safety_classify` (ADR 0005). If risk=high: mark user message as
     flagged, return helpline payload, do NOT advance.
  4. Else: call AI `chat` with the FULL history (stateless AI, ADR 0002).
  5. Persist AI message with `prompt_version`.
  6. Update `chat.current_stage` from the response. Set `is_done` if final.
  7. If stage is `summary`/`final` and structured fields are present, upsert a
     Dream row from the structured payload (replaces legacy `_parse_summary`,
     ADR 0003).
  8. Log the turn's token counts.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from collections.abc import AsyncIterator, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import anyio
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.config import settings
from ....core.dependencies import get_current_user, get_db, require_consent
from ....core.limits import count_ai_request
from ....db.models.chat import Chat
from ....db.models.chat_message import ChatMessage
from ....db.models.dream import Dream
from ....db.models.user import User
from ....db.repositories.chat_message_repo import ChatMessageRepo
from ....db.repositories.chat_repo import ChatRepo
from ....db.repositories.dream_repo import DreamRepo
from ....services.ai_client import AIServiceError, BaseAiClient, get_ai_client
from ....services.rehearsal_audio import clear_rehearsal, discard_dream_audio
from .schemas import (
    ChatIntensityRequest,
    ChatIntensityResponse,
    ChatMessageOut,
    ChatMessageRequest,
    ChatMessageResponse,
    ChatOut,
    ChatStartRequest,
    ChatStartResponse,
    SafetyEventPayload,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/chats", tags=["chats"])

_TITLE_MAX_LEN = 60

# Matches the first standalone 0-10 in an intensity-stage user message
# ("8", "8/10", "It is an 8 out of 10."). This parses USER input, not AI
# output — the ADR 0003 ban on string-matching AI responses doesn't apply.
_INTENSITY_RE = re.compile(r"\b(10|[0-9])\b")


def _truncate_title(text: str) -> str:
    title = " ".join(text.split())
    if len(title) <= _TITLE_MAX_LEN:
        return title
    return title[: _TITLE_MAX_LEN - 1].rstrip() + "…"


def _extract_intensity(structured: dict[str, object], history: list[ChatMessage]) -> int | None:
    """The user's 0-10 rating: structured AI field first, history regex fallback.

    The structured field is authoritative (the summary model reads the whole
    transcript); the regex only catches sessions where the model omitted it.
    """
    raw = structured.get("intensity")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and 0 <= raw <= 10:
        return int(raw)
    for m in reversed(history):
        if m.role == "user" and m.stage == "intensity":
            match = _INTENSITY_RE.search(m.content)
            if match:
                return int(match.group(1))
    return None


def _to_message_out(msg: ChatMessage) -> ChatMessageOut:
    return ChatMessageOut(
        id=msg.id,
        role=msg.role,
        content=msg.content,
        stage=msg.stage,
        created_at=msg.created_at,
    )


def _to_chat_out(chat) -> ChatOut:
    return ChatOut(
        id=chat.id,
        language=chat.language,
        current_stage=chat.current_stage,
        is_done=chat.is_done,
        is_nightmare=chat.is_nightmare,
        title=chat.title,
        created_at=chat.created_at,
        updated_at=chat.updated_at,
    )


# ---------------------------------------------------------------------------


@router.post(
    "/start",
    response_model=ChatStartResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_consent)],
)
async def start_chat(
    body: ChatStartRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChatStartResponse:
    chat = await ChatRepo(db).create(user_id=user.id, language=body.language)
    return ChatStartResponse(
        chat_id=chat.id, language=chat.language, current_stage=chat.current_stage
    )


@router.get("", response_model=list[ChatOut])
async def list_chats(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ChatOut]:
    rows = await ChatRepo(db).list_for_user(user.id)
    return [_to_chat_out(c) for c in rows]


@router.get("/{chat_id}/messages", response_model=list[ChatMessageOut])
async def list_messages(
    chat_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ChatMessageOut]:
    chat = await ChatRepo(db).get_for_user(user.id, chat_id)
    if chat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "chat not found")
    msgs = await ChatMessageRepo(db).list_for_chat(chat_id)
    return [_to_message_out(m) for m in msgs]


@router.delete("/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat(
    chat_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    repo = ChatRepo(db)
    chat = await repo.get_for_user(user.id, chat_id)
    if chat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "chat not found")
    await repo.delete(chat)


@router.put(
    "/{chat_id}/intensity",
    response_model=ChatIntensityResponse,
    dependencies=[Depends(require_consent)],
)
async def set_intensity(
    chat_id: int,
    body: ChatIntensityRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChatIntensityResponse:
    """App-owned intensity: the routed chat no longer elicits the 0-10 rating,
    so the client collects it at session end and attaches it to the chat's
    Dream (one chat → one dream via `dream.session_id`, ADR 0003). The rating
    doesn't touch the rewritten text, so cached rehearsal audio stays valid.
    """
    chat = await ChatRepo(db).get_for_user(user.id, chat_id)
    if chat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "chat not found")
    repo = DreamRepo(db)
    dream = await repo.get_for_user_by_session(user.id, str(chat.id))
    if dream is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no dream for this chat yet")
    dream.intensity = body.intensity
    dream.last_edited = datetime.now(UTC)
    await repo.save(dream)
    return ChatIntensityResponse(chat_id=chat.id, dream_id=dream.id, intensity=body.intensity)


# ---------------------------------------------------------------------------
# The interesting ones: POST /{chat_id}/message and its SSE sibling.
# The shared gates (404/409/persist/safety) and the shared post-AI
# persistence live in _pre_turn / _finalize_ai_turn so the two routes cannot
# drift semantically.
# ---------------------------------------------------------------------------


@dataclass
class _TurnSetup:
    """Everything the AI-call step needs, produced by the shared gates."""

    chat: Chat
    user_msg: ChatMessage
    history_msgs: list[ChatMessage]
    safety_payload: SafetyEventPayload | None  # non-None ⇒ high risk, no AI turn
    language_switched: bool = False  # this message switched chat.language
    previous_language: str | None = None  # set when language_switched

    @property
    def switch_note(self) -> bool:
        """Send `language_switched` to the AI: this message switched, or an
        earlier crisis message did and no AI turn has followed since."""
        return self.language_switched or self.chat.language_switch_pending


async def _revert_language_switch(db: AsyncSession, setup: _TurnSetup) -> None:
    """Undo this turn's language switch when its user message is rolled back:
    the client's retry of the same text then switches (and notes it) again."""
    if setup.language_switched and setup.previous_language:
        setup.chat.language = setup.previous_language
        await ChatRepo(db).save(setup.chat)


async def _pre_turn(
    chat_id: int,
    body: ChatMessageRequest,
    user: User,
    db: AsyncSession,
    ai: BaseAiClient,
) -> _TurnSetup:
    """Shared pre-AI gates: 404/409 → daily AI-request cap (429) → persist
    user message → title → language switch → safety classifier (ADR 0005, 502
    on failure with the user message dropped and the switch undone, high-risk
    short-circuit).

    Raises HTTPException for every failure, so the streaming route runs this
    BEFORE the response starts and errors arrive as plain JSON, exactly like
    the non-streaming route.
    """
    chat_repo = ChatRepo(db)
    msg_repo = ChatMessageRepo(db)

    chat = await chat_repo.get_for_user(user.id, chat_id)
    if chat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "chat not found")
    if chat.is_done:
        raise HTTPException(status.HTTP_409_CONFLICT, "chat is already finished")

    # ---- daily AI-request cap (core/limits) -------------------------------
    # Counted at admission, before anything is stored or sent to a model: a
    # turn that fails later still counts.
    count_ai_request(user.id)

    # ---- persist user message --------------------------------------------
    user_msg = ChatMessage(
        chat_id=chat.id,
        role="user",
        content=body.content,
        stage=chat.current_stage,
    )
    await msg_repo.create(user_msg)

    if not chat.title:
        chat.title = _truncate_title(body.content)
        await chat_repo.save(chat)

    # ---- language switch (en <-> de) -------------------------------------
    # Before the classifier (and the dev-flag skip), so the crisis check, the
    # helpline text and the AI turn all use the language of THIS message. The
    # check never raises; any failure keeps the language. Not counted against
    # the daily cap: it is part of the turn admitted above.
    language_switched = False
    previous_language = chat.language
    if body.content.strip():
        new_language = await ai.language_check(body.content, chat.language)
        if new_language != chat.language and new_language in ("en", "de"):
            # No chat id: the crisis warning may follow from the same request.
            log.info("chat language switched %s -> %s", chat.language, new_language)
            chat.language = new_language
            await chat_repo.save(chat)
            language_switched = True

    history_msgs = await msg_repo.list_for_chat(chat.id)

    # ---- safety classifier (ADR 0005) ------------------------------------
    # Stage + the assistant message being answered disambiguate terse replies
    # ("It is an 8 out of 10." is a scale answer, not ideation).
    if not settings.safety_classifier_enabled:
        # DEV-ONLY (SAFETY_CLASSIFIER_ENABLED=false): skip the crisis
        # pre-classifier while its prompt misfires (2026-07-13 false
        # positive). Never off in prod — the re-enable gate is in ROADMAP.
        log.warning(
            "safety classifier DISABLED by dev flag — skipping (chat_id=%s)",
            chat.id,
        )
        return _TurnSetup(
            chat=chat,
            user_msg=user_msg,
            history_msgs=history_msgs,
            safety_payload=None,
            language_switched=language_switched,
            previous_language=previous_language if language_switched else None,
        )
    last_assistant = next(
        (m.content for m in reversed(history_msgs) if m.role == "assistant"),
        None,
    )
    try:
        safety = await ai.safety_classify(
            {
                "text": body.content,
                "language": chat.language,
                "stage": chat.current_stage,
                "context": last_assistant,
            }
        )
    except AIServiceError as exc:
        # Same cleanup as the chat-failure path: the turn never ran, and an
        # orphaned user message would be replayed alongside the client's retry.
        await msg_repo.delete(user_msg)
        if language_switched:
            # Like a failed AI turn: undo the switch, so the client's retry of
            # the same text switches (and notes it) again.
            chat.language = previous_language
            await chat_repo.save(chat)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"safety: {exc}") from exc

    safety_payload: SafetyEventPayload | None = None
    if safety.get("risk") == "high":
        user_msg.safety_flagged = True
        if language_switched:
            # No AI turn follows, so the switch note waits for the next one.
            chat.language_switch_pending = True
        await db.commit()
        # No ids, categories or text: logs outlive account deletion.
        log.warning("crisis check: high risk")
        safety_payload = SafetyEventPayload(
            risk="high",
            categories=list(safety.get("categories", [])),
            confidence=float(safety.get("confidence", 0.0)),
            # HELPLINE_TEXT_EN / HELPLINE_TEXT_DE; the client renders it
            # in place of the AI reply.
            message=settings.helpline_text_for(chat.language),
        )

    return _TurnSetup(
        chat=chat,
        user_msg=user_msg,
        history_msgs=history_msgs,
        safety_payload=safety_payload,
        language_switched=language_switched,
        previous_language=previous_language if language_switched else None,
    )


def _ai_chat_payload(
    chat: Chat, history_msgs: list[ChatMessage], *, language_switched: bool = False
) -> dict[str, Any]:
    """The stateless AI request (ADR 0002): full history + echo state.

    Safety-flagged messages got a helpline, not an AI turn — replaying them
    into later prompts would re-inject crisis content the flow already
    short-circuited on.

    `session_id` is the chat's id; the ported core keys its conversation by it
    (never state). `language_switched` is true on the turn whose message
    switched `chat.language` (or on the first AI turn after a crisis message
    that switched it); the AI then notes the change in its reply.
    """
    return {
        "messages": [
            {
                "role": m.role,
                "content": m.content,
                "stage": m.stage,
                "language": chat.language,
            }
            for m in history_msgs
            if not m.safety_flagged
        ],
        "language": chat.language,
        "session_id": str(chat.id),
        # Echo the once-generated nightmare summary back to the stateless AI
        # so it never regenerates it (None on the first turns is fine).
        "nightmare_summary": chat.nightmare_summary,
        "language_switched": language_switched,
    }


async def _finalize_ai_turn(
    *,
    db: AsyncSession,
    user: User,
    chat: Chat,
    history_msgs: list[ChatMessage],
    ai_resp: dict[str, Any],
) -> ChatMessage:
    """Shared post-AI persistence: AI message row, chat advance (incl. the
    nightmare-summary echo), Dream upsert (ADR 0003), token-count log line.
    """
    ai_stage = str(ai_resp.get("stage", chat.current_stage))
    ai_text = str(ai_resp.get("response", ""))
    structured = ai_resp.get("structured")
    usage = ai_resp.get("usage", {}) or {}
    prompt_version = str(ai_resp.get("prompt_version", "unknown"))
    nightmare_summary = ai_resp.get("nightmare_summary")

    # ---- persist AI message ----------------------------------------------
    ai_msg = ChatMessage(
        chat_id=chat.id,
        role="assistant",
        content=ai_text,
        stage=ai_stage,
        prompt_version=prompt_version,
    )
    await ChatMessageRepo(db).create(ai_msg)

    # ---- advance chat state ----------------------------------------------
    chat.current_stage = ai_stage
    chat.is_done = ai_stage == "final"
    chat.language_switch_pending = False  # this turn carried the switch note
    if ai_stage == "intensity" and chat.is_nightmare is not True:
        chat.is_nightmare = True
    if isinstance(nightmare_summary, str) and nightmare_summary:
        chat.nightmare_summary = nightmare_summary
    chat.updated_at = datetime.now(UTC)
    await ChatRepo(db).save(chat)

    # ---- structured-summary -> Dream (ADR 0003) --------------------------
    if structured and ai_stage in ("summary", "final"):
        await _upsert_dream_from_structured(
            db=db,
            user_id=user.id,
            chat_id=chat.id,
            language=chat.language,
            structured=structured,
            intensity=_extract_intensity(structured, history_msgs),
        )

    # ---- token usage: counts only, logs outlive account deletion ---------
    if usage:
        log.info(
            "chat.usage input_tokens=%s output_tokens=%s",
            usage.get("input_tokens", 0),
            usage.get("output_tokens", 0),
        )
    return ai_msg


@router.post(
    "/{chat_id}/message",
    response_model=ChatMessageResponse,
    dependencies=[Depends(require_consent)],
)
async def send_message(
    chat_id: int,
    body: ChatMessageRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    ai: BaseAiClient = Depends(get_ai_client),
) -> ChatMessageResponse:
    setup = await _pre_turn(chat_id, body, user, db, ai)
    chat = setup.chat

    if setup.safety_payload is not None:
        return ChatMessageResponse(
            chat_id=chat.id,
            user_message=_to_message_out(setup.user_msg),
            ai_message=None,
            stage=chat.current_stage,
            is_done=chat.is_done,
            safety_event=setup.safety_payload,
        )

    try:
        ai_resp = await ai.chat(
            _ai_chat_payload(chat, setup.history_msgs, language_switched=setup.switch_note)
        )
    except AIServiceError as exc:
        # Drop the just-persisted user message: leaving it orphaned (no AI
        # reply) would replay it into the next turn's history alongside the
        # client's retry of the same text.
        await ChatMessageRepo(db).delete(setup.user_msg)
        await _revert_language_switch(db, setup)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"chat: {exc}") from exc

    ai_msg = await _finalize_ai_turn(
        db=db,
        user=user,
        chat=chat,
        history_msgs=setup.history_msgs,
        ai_resp=ai_resp,
    )
    return ChatMessageResponse(
        chat_id=chat.id,
        user_message=_to_message_out(setup.user_msg),
        ai_message=_to_message_out(ai_msg),
        stage=chat.current_stage,
        is_done=chat.is_done,
        safety_event=None,
    )


# ---------------------------------------------------------------------------
# POST /{chat_id}/message/stream — SSE to the Flutter client (ADR 0009).
#
# Frame contract (each frame is one `data: <payload>\n\n` event):
#   1. data: {"user_message": <ChatMessageOut>}            — always first
#   2a. high-risk: data: {"safety_event": <SafetyEventPayload>}  then [DONE]
#   2b. normal:    data: {"content": "<delta>", ...}       — passed through
#                  from the AI stream; clients append `.content` in order
#   3b.            data: {"done": {"chat_id", "ai_message": <ChatMessageOut>,
#                                  "stage", "is_done"}}    — persisted turn
#   4. data: [DONE]                                        — always terminal
# Mid-stream failure: data: {"error": "<msg>"} then [DONE]; the user message
# was rolled back (parity with the non-streaming 502 path) so the client's
# retry flow can resend it. Pre-stream failures (404/409/502) raise before
# streaming starts and arrive as plain JSON errors, same as /message.
# The AI's internal frames that are not part of this contract (its own `final`,
# clinical `safety`, `[DONE]`) are consumed here and never forwarded.
# ---------------------------------------------------------------------------

_SSE_DONE = "data: [DONE]\n\n"
# Upper bound for the cleanup after a client disconnect.
_ROLLBACK_TIMEOUT_SECONDS = 8
# Chat streams that have not finished yet. On a stop uvicorn cancels open
# responses after --timeout-graceful-shutdown, runs the app's shutdown and
# then re-raises SIGTERM, which ends the process before a cancelled stream
# gets to roll back: the shutdown (main.lifespan) waits for these first.
_open_streams: set[asyncio.Future[None]] = set()


async def wait_for_open_streams(limit_seconds: float) -> None:
    """Wait until every open chat stream has finished, its cleanup included."""
    if _open_streams:
        await asyncio.wait(set(_open_streams), timeout=limit_seconds)


async def _finish_despite_cancel(cleanup: Coroutine[Any, Any, None], limit_seconds: float) -> None:
    """Run `cleanup` to its end inside a task that is being cancelled.

    On a disconnect Starlette cancels the response's anyio scope, which
    re-cancels every await in it (the shield stops that); at shutdown uvicorn
    and then asyncio.run cancel the task natively (waited out here: asyncio.run
    lets cancelled tasks finish). Gives up after `limit_seconds`."""
    with anyio.CancelScope(shield=True):
        task = asyncio.ensure_future(cleanup)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + limit_seconds
        while not task.done():
            remaining = deadline - loop.time()
            if remaining <= 0:
                task.cancel()
                log.error("disconnect cleanup did not finish within %ss", limit_seconds)
                # Let it unwind before the request's session is closed under it.
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.wait({task}, timeout=1)
                return
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait({task}, timeout=remaining)


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


async def _stream_ai_turn(
    db: AsyncSession,
    ai: BaseAiClient,
    user: User,
    setup: _TurnSetup,
) -> AsyncIterator[str]:
    chat = setup.chat
    # Snapshot for error paths: after a failed flush the session is poisoned
    # and rollback expires ORM attributes — touching chat.id in a handler
    # would itself raise PendingRollbackError.
    chat_id = chat.id
    finalize_started = False

    async def _rollback_user_msg() -> None:
        """Best-effort removal of the pre-persisted user message.

        Resets the session first: after a failed flush every ORM access raises
        until Session.rollback() runs. Must never raise itself."""
        try:
            await db.rollback()
            await ChatMessageRepo(db).delete(setup.user_msg)
            await _revert_language_switch(db, setup)
        except Exception:
            log.exception("failed to roll back user message for chat %s", chat_id)

    # Registered until the generator has finished, cleanup included: the
    # app's shutdown waits for it (wait_for_open_streams).
    closed = asyncio.get_running_loop().create_future()
    _open_streams.add(closed)
    try:
        final: dict[str, Any] | None = None
        try:
            yield _sse({"user_message": _to_message_out(setup.user_msg).model_dump(mode="json")})

            if setup.safety_payload is not None:
                yield _sse({"safety_event": setup.safety_payload.model_dump(mode="json")})
                yield _SSE_DONE
                return

            # aclosing: a disconnect closes the AI stream (and the provider
            # response under it) right away, not whenever GC gets to it.
            async with contextlib.aclosing(
                ai.chat_stream(
                    _ai_chat_payload(chat, setup.history_msgs, language_switched=setup.switch_note)
                )
            ) as frames:
                async for frame in frames:
                    body = frame.removeprefix("data:").strip()
                    if body == "[DONE]":
                        break
                    try:
                        data = json.loads(body)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(data, dict):
                        continue
                    if "error" in data:
                        raise AIServiceError(str(data["error"]))
                    if isinstance(data.get("final"), dict):
                        final = data["final"]
                    elif "content" in data:
                        yield f"data: {body}\n\n"
                    # anything else (e.g. the clinical safety frame) is internal — the
                    # non-streaming route doesn't surface it to the client either
            if final is None:
                raise AIServiceError("AI stream ended without a final frame")
            finalize_started = True
            ai_msg = await _finalize_ai_turn(
                db=db,
                user=user,
                chat=chat,
                history_msgs=setup.history_msgs,
                ai_resp=final,
            )
            yield _sse(
                {
                    "done": {
                        "chat_id": chat.id,
                        "ai_message": _to_message_out(ai_msg).model_dump(mode="json"),
                        "stage": chat.current_stage,
                        "is_done": chat.is_done,
                    }
                }
            )
        except (asyncio.CancelledError, GeneratorExit):
            # Client disconnected / abandoned the turn mid-stream (chat switch,
            # app kill, network loss). These are BaseExceptions — an
            # `except Exception` never sees them. No frames can be emitted; the
            # only question is DB state: before finalize, remove the user message
            # so the orphan is never replayed into the next turn's history; once
            # finalize has begun, keep whatever committed (the client's refetch
            # shows the completed turn). The high-risk safety path never rolls
            # back: the flagged message is the turn the helpline answered
            # (ADR 0005) and must survive a disconnect.
            if setup.safety_payload is None and not finalize_started:
                await _finish_despite_cancel(_rollback_user_msg(), _ROLLBACK_TIMEOUT_SECONDS)
            raise
        except Exception as exc:
            # Headers are already sent — surface the failure as an error frame.
            log.exception("mid-stream failure in /chats/%s/message/stream", chat_id)
            if finalize_started:
                # _finalize_ai_turn commits step by step: the AI message (and
                # possibly the stage advance) may already be persisted. Deleting
                # the user message now would orphan the REPLY — keep both rows,
                # exactly like the non-streaming route's 500 on the same failure.
                try:
                    await db.rollback()  # un-poison the session; keep committed rows
                except Exception:
                    log.exception("session rollback failed for chat %s", chat_id)
            else:
                # AI stream failed before anything beyond the user message was
                # written — same rollback as the non-streaming 502 path.
                await _rollback_user_msg()
            yield _sse({"error": str(exc)})
        yield _SSE_DONE
    finally:
        _open_streams.discard(closed)
        closed.set_result(None)


@router.post("/{chat_id}/message/stream", dependencies=[Depends(require_consent)])
async def send_message_stream(
    chat_id: int,
    body: ChatMessageRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    ai: BaseAiClient = Depends(get_ai_client),
) -> StreamingResponse:
    """Streaming variant of /message (ADR 0009). Frame contract: see above."""
    setup = await _pre_turn(chat_id, body, user, db, ai)
    return StreamingResponse(
        _stream_ai_turn(db, ai, user, setup),
        media_type="text/event-stream",
    )


async def _upsert_dream_from_structured(
    *,
    db: AsyncSession,
    user_id,
    chat_id: int,
    language: str,
    structured: dict,
    intensity: int | None = None,
) -> Dream:
    """Create or update the Dream row that corresponds to this chat's summary.

    `chat_id` (as a string) is used as `dream.session_id`; one chat → one dream.
    `language` is the chat's language at the summary, so the journal entry and
    its rehearsal audio follow a mid-chat switch.
    """
    repo = DreamRepo(db)
    session_key = str(chat_id)

    title = str(structured.get("title", "Untitled dream"))
    rewritten = str(structured.get("rewritten_summary") or structured.get("rewritten_dream", ""))
    original = str(
        structured.get("dream_summary")
        or structured.get("original_summary")
        or structured.get("original_dream", "")
    )

    existing = await repo.get_for_user_by_session(user_id, session_key)
    if existing is not None:
        existing.title = title
        existing.language = language
        existing.original_dream = original or existing.original_dream
        existing.rewritten_dream = rewritten or existing.rewritten_dream
        if intensity is not None:
            existing.intensity = intensity
        existing.last_edited = datetime.now(UTC)
        # Rewritten changed → invalidate any cached rehearsal audio.
        await discard_dream_audio(user_id, existing.id)
        clear_rehearsal(existing)
        return await repo.save(existing)

    dream = Dream(
        user_id=user_id,
        title=title,
        original_dream=original,
        rewritten_dream=rewritten,
        intensity=intensity,
        is_nightmare=True,
        session_id=session_key,
        source="chatbot",
        language=language,
    )
    return await repo.create(dream)
