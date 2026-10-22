"""Bound request bodies before FastAPI parses JSON or spools uploaded files."""

from fastapi import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_AUDIO_BYTES = 25 * 1024 * 1024
MAX_AUDIO_BODY_BYTES = MAX_AUDIO_BYTES + 1024 * 1024  # multipart framing
MAX_JSON_BODY_BYTES = 1024 * 1024


class RequestSizeLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = (
            MAX_AUDIO_BODY_BYTES
            if scope["path"] == "/api/v1/audio/transcribe"
            else MAX_JSON_BODY_BYTES
        )
        lengths = [value for name, value in scope["headers"] if name.lower() == b"content-length"]
        if lengths:
            if len(lengths) != 1 or len(lengths[0]) > 20 or not lengths[0].isdigit():
                await JSONResponse({"detail": "invalid content length"}, 400)(scope, receive, send)
                return
            if int(lengths[0]) > limit:
                await JSONResponse({"detail": "request body too large"}, 413)(scope, receive, send)
                return

        received = 0

        async def bounded_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    # FastAPI preserves HTTPException during body parsing. This
                    # also covers chunked bodies with no Content-Length header.
                    raise HTTPException(413, "request body too large")
            return message

        await self.app(scope, bounded_receive, send)
