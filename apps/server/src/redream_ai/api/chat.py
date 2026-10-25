"""chat + chat_stream — ported IRT chatbot core (recording → rewriting → summary → final).

Adapter over the ported orchestrator (`redream_ai.core.irt_app.process_chat_message`
and its streaming sibling `process_chat_message_stream`). The AI side stays
stateless (ADR 0002): the backend sends the full history each turn, we rebuild a
ported `Conversation` from it, run one turn, and map the result back to the wire
`ChatResponse`. The summary stage additionally runs a structured extraction pass
(ADR 0003). The neuro-symbolic `SafetyCritic` runs inside the ported core during
`rewriting` only. The backend calls both functions in-process
(`redream_api.services.ai_client`); neither is reachable over HTTP.

SSE frame contract for `chat_stream` (ADR 0009). Every item it yields is one
`data: <payload>\\n\\n` frame; on success the order is:

1. Zero or more content chunks, passed through VERBATIM from the ported core:
   ``data: {"content": "<text chunk>", "stages": ["<stage>", ...], "language": "en"}``
   (`stages` already includes THIS turn's resolved stage, appended by the router
   before generation starts.)
2. At most one safety frame (rewriting stage only, when the SafetyCritic ran),
   passed through verbatim: ``data: {"safety": {...}}``
3. Exactly one final metadata frame, added by this adapter so the stateless
   backend can persist the turn without a second call:
   ``data: {"final": {<ChatResponse JSON>}}`` where the object carries the same
   fields as the non-streaming response: `stage`, `response` (the full
   accumulated text), `structured` (summary stage only), `usage` (the response
   generation's input/output token counts, same semantics as `chat`; zeros if the
   core exposed none), `prompt_version`, `safety`, `nightmare_summary`.
4. Terminal sentinel, always the last frame: ``data: [DONE]``

The ported core also emits one INTERNAL ``data: {"usage": {"input": N, "output":
N, "total": N}}`` frame at end of generation; this adapter consumes it to fill
`final.usage` and never forwards it — backend consumers only ever see the four
frame kinds above.

On failure (before or mid-stream) the content frames seen so far are followed by
``data: {"error": "<message>"}`` and then the terminal ``data: [DONE]`` — no
`final` frame is emitted. Request validation errors (400/503) raise HTTPException
from the `chat_stream` call itself, before any frame, exactly like `chat`.
The stream closes the core's generator (and through it the provider response)
as soon as the consumer closes it.
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import HTTPException

from ..config import LLM_REQUIRED
from ..core import irt_app
from ..core.models import ChatInput as PortedChatInput
from ..core.models import Conversation as PortedConversation
from ..core.models import Message as PortedMessage
from ..models import ChatRequest, ChatResponse, Role, Stage, SummaryStructured, UsageStats
from ..summary_structured import extract_summary_structured

log = logging.getLogger(__name__)

# The ported prompts carry no version constant; stamp the wire contract's required
# `prompt_version` with the ported prompt lineage so callers can pin behaviour.
PROMPT_VERSION = "irt-restructured-2026-10-25"


def _rebuild_conversation(req: ChatRequest) -> PortedConversation:
    """Rebuild the ported Conversation from the history BEFORE the new user message.

    The ported `process_chat_message` appends the new user message itself, so it must
    NOT already be in `conversation.messages`. `conversation.stages` is the resolved
    stage per prior turn — one per prior assistant message, in order.
    """
    history = req.messages[:-1]  # everything except the new user turn
    messages = [
        PortedMessage(
            content=m.content,
            role=m.role.value,
            stage=(m.stage.value if m.stage else None),
            language=(m.language.value if m.language else None),
        )
        for m in history
    ]
    stages = [m.stage.value for m in history if m.role == Role.ASSISTANT and m.stage is not None]
    return PortedConversation(
        session_id=req.session_id or "anonymous",
        messages=messages,
        stages=stages,
        language=req.language.value,
        nightmare_summary=req.nightmare_summary,
    )


def _validate_chat_request(req: ChatRequest) -> None:
    """Shared request validation for chat and chat_stream (same errors, same order)."""
    if not req.messages:
        raise HTTPException(400, "messages must not be empty")
    if irt_app.routing_agent is None or irt_app.response_agent is None:
        raise HTTPException(503, f"IRT agents not configured (set {LLM_REQUIRED})")
    if req.messages[-1].role != Role.USER:
        raise HTTPException(400, "the last message must be the new user message")


def _ported_input(req: ChatRequest, conversation: PortedConversation) -> PortedChatInput:
    return PortedChatInput(
        session_id=conversation.session_id,
        message=req.messages[-1].content,
        language_override=req.language.value,
        language_switched=req.language_switched,
    )


async def _build_chat_response(
    stage_str: str,
    full_text: str,
    *,
    req: ChatRequest,
    conversation: PortedConversation,
    safety: dict[str, Any] | None,
    usage: dict[str, Any] | None,
) -> ChatResponse:
    """The ONE wire-response builder — `chat` returns it, `chat_stream` embeds it
    in the `final` frame. Any new ChatResponse field belongs here, never in one
    entry point only. Raises ValueError on an unmapped ported stage; each caller
    wraps that in its own error shape (HTTPException 500 vs SSE error frame).
    """
    stage = Stage(stage_str)  # ValueError on anything the wire enum doesn't know
    structured: SummaryStructured | None = None
    if stage == Stage.SUMMARY:
        structured = await extract_summary_structured(full_text, req.language)
    return ChatResponse(
        stage=stage,
        response=full_text,
        structured=structured,
        # Ported usage dicts carry {input, output, total}; zeros when absent.
        usage=UsageStats(
            input_tokens=(usage or {}).get("input", 0),
            output_tokens=(usage or {}).get("output", 0),
        ),
        prompt_version=PROMPT_VERSION,
        safety=safety,
        # Echo the once-generated nightmare summary so the backend can persist it
        # and send it back next turn (ChatRequest.nightmare_summary) — avoids
        # regenerating.
        nightmare_summary=conversation.nightmare_summary,
    )


async def chat(req: ChatRequest) -> ChatResponse:
    """One non-streaming IRT turn (SSE streaming variant: `chat_stream`, ADR 0009)."""
    _validate_chat_request(req)

    conversation = _rebuild_conversation(req)
    ported = await irt_app.process_chat_message(
        _ported_input(req, conversation), conversation, safety_enabled=True
    )

    try:
        return await _build_chat_response(
            ported.stage,
            ported.response,
            req=req,
            conversation=conversation,
            safety=ported.safety,
            usage=ported.usage,
        )
    except ValueError as exc:  # ported core should only emit recording/rewriting/summary/final
        log.error("ported core returned unmapped stage %r", ported.stage)
        raise HTTPException(500, f"internal stage mapping error: {ported.stage}") from exc


# ---------------------------------------------------------------------------
# chat_stream — SSE frames (ADR 0009). Frame contract: see module docstring.
# ---------------------------------------------------------------------------

_DONE_FRAME = "data: [DONE]\n\n"


def _frame_payload(frame: str) -> dict[str, Any] | None:
    """Decode an SSE frame's JSON payload; None if it isn't a JSON object."""
    body = frame.strip().removeprefix("data:").strip()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


async def _final_frame(
    req: ChatRequest,
    conversation: PortedConversation,
    full_text: str,
    safety: dict[str, Any] | None,
    usage: dict[str, Any] | None,
) -> str:
    """Build the ChatResponse-shaped `final` metadata frame (frame 3 of the contract).

    The ported generator resolves this turn's stage up front (`determine_stage_async`
    appends it to `conversation.stages` before generation starts), so once the
    generator is exhausted `stages[-1]` is THIS turn's stage. An empty `stages` list
    or an unmapped ported stage raises here and surfaces as an error frame upstream.
    """
    if not conversation.stages:
        log.error("ported stream ended with an empty stages list")
        raise ValueError("ported stream resolved no stage")
    try:
        final = await _build_chat_response(
            conversation.stages[-1],
            full_text,
            req=req,
            conversation=conversation,
            safety=safety,
            usage=usage,
        )
    except ValueError:
        # Same log as `chat`; the error shape differs (frames are already out,
        # so this becomes an error frame, not an HTTPException).
        log.error("ported core returned unmapped stage %r", conversation.stages[-1])
        raise
    return f"data: {json.dumps({'final': final.model_dump(mode='json')})}\n\n"


async def _stream_turn(
    req: ChatRequest,
    conversation: PortedConversation,
    chat_input: PortedChatInput,
) -> AsyncGenerator[str, None]:
    """Pass the ported core's SSE frames through verbatim, then add `final` + [DONE].

    The ported generator's own terminal `data: [DONE]` is swallowed so that OUR
    [DONE] stays the last frame (after the `final` metadata frame). Content chunks
    are accumulated along the way to build the final frame's full `response`.
    """
    chunks: list[str] = []
    safety: dict[str, Any] | None = None
    usage: dict[str, Any] | None = None
    try:
        errored = False
        # aclosing: when our consumer stops early (client gone), the core's
        # generator closes now, not whenever GC finalizes it.
        async with contextlib.aclosing(
            irt_app.process_chat_message_stream(chat_input, conversation, safety_enabled=True)
        ) as stream:
            async for frame in stream:
                if frame.strip() == "data: [DONE]":
                    continue  # swallowed — ours is emitted after the final frame
                payload = _frame_payload(frame)
                if payload is not None:
                    if "error" in payload:
                        # Ported core caught an exception mid-stream: pass its error
                        # frame through and terminate; no final frame on failure.
                        errored = True
                        yield frame
                        break
                    usage_payload = payload.get("usage")
                    if isinstance(usage_payload, dict):
                        # Internal frame from the ported core — folded into `final`,
                        # never forwarded (see module docstring).
                        usage = usage_payload
                        continue
                    content = payload.get("content")
                    if isinstance(content, str):
                        chunks.append(content)
                    safety_payload = payload.get("safety")
                    if isinstance(safety_payload, dict):
                        safety = safety_payload
                yield frame
        if not errored:
            yield await _final_frame(req, conversation, "".join(chunks), safety, usage)
    except Exception as exc:
        # Never leave the stream half-open: any failure (inside the ported
        # generator or while building the final frame) becomes an error frame.
        log.exception("mid-stream failure in chat_stream")
        yield f"data: {json.dumps({'error': str(exc)})}\n\n"
    yield _DONE_FRAME


def chat_stream(req: ChatRequest) -> AsyncGenerator[str, None]:
    """Streaming IRT turn as SSE frames (ADR 0009). Frame contract: module docstring.

    Validates eagerly: a rejected request raises HTTPException here, before any
    frame — the caller closes the returned generator (`contextlib.aclosing`).
    """
    _validate_chat_request(req)

    conversation = _rebuild_conversation(req)
    return _stream_turn(req, conversation, _ported_input(req, conversation))
