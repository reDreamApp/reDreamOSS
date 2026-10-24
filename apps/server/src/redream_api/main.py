"""FastAPI app factory.

Run with:
    uvicorn redream_api.main:app --host 0.0.0.0 --port 8001
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

from .api.router import router_v1
from .api.v1.chats.routes import wait_for_open_streams
from .core import passwords
from .core.config import legacy_setting_names, settings
from .core.logging import configure_logging
from .core.request_limits import RequestSizeLimitMiddleware
from .services.ai_client import get_ai_client

configure_logging()
log = logging.getLogger(__name__)

# Within compose's stop_grace_period (30 s) after uvicorn's
# --timeout-graceful-shutdown (20 s).
_SHUTDOWN_STREAM_WAIT_SECONDS = 8


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # A .env carried over from reDreamAI: say why no provider is configured.
    legacy = legacy_setting_names(os.environ)
    if legacy:
        log.warning(
            "ignored reDreamAI settings: %s — reDreamOSS reads LLM_*, STT_* and TTS_* "
            "instead (see .env.example)",
            ", ".join(legacy),
        )
    # The AI side runs in-process: build its client (IRT agents, SafetyCritic,
    # crisis-classifier clients) at startup, not on the first chat turn, and
    # log what is ready.
    get_ai_client()
    # The dummy hash unknown-email logins verify against (core/passwords).
    await passwords.prepare_dummy_hash()
    yield
    # Chat streams that uvicorn just cancelled roll back their unanswered
    # message; uvicorn ends the process right after this returns.
    await wait_for_open_streams(_SHUTDOWN_STREAM_WAIT_SECONDS)


app = FastAPI(
    title="reDreamOSS server",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(RequestSizeLimitMiddleware)


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # A 422 echoes each refused value; on the auth routes that would be a
    # password or an invite code, so their errors leave the value out.
    if request.url.path.startswith("/api/v1/auth/"):
        exc = RequestValidationError(
            [{k: v for k, v in error.items() if k != "input"} for error in exc.errors()]
        )
    return await request_validation_exception_handler(request, exc)


app.include_router(router_v1, prefix="/api/v1")


class _WebApp(StaticFiles):
    """The built web app. Not in a frame on another site (it signs itself in on
    a personal server, so a framing page could click through it), and an
    unknown /api/... path stays the API's JSON 404, not a file lookup."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        if path == "api" or path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        response = await super().get_response(path, scope)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        return response


def _mount_web_app(directory: str) -> None:
    """The web app at /, on the same origin as the API (no CORS needed).
    Mounted after the API's routes, which match first."""
    if not directory:
        return
    web = Path(directory)
    if not (web / "index.html").is_file():
        log.warning("WEB_DIR has no index.html; the web app is not served")
        return
    app.mount("/", _WebApp(directory=web, html=True), name="web")


_mount_web_app(settings.web_dir)
