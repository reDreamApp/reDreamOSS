"""FastAPI app factory.

Run with:
    uvicorn redream_api.main:app --host 0.0.0.0 --port 8001
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .api.router import router_v1
from .core import passwords
from .core.logging import configure_logging
from .core.request_limits import RequestSizeLimitMiddleware
from .services.ai_client import get_ai_client

configure_logging()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # The AI side runs in-process: build its client (IRT agents, SafetyCritic,
    # crisis-classifier clients) at startup, not on the first chat turn, and
    # log what is ready.
    get_ai_client()
    # The dummy hash unknown-email logins verify against (core/passwords).
    await passwords.prepare_dummy_hash()
    yield


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
