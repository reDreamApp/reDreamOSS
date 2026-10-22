"""FastAPI app factory.

Run with:
    uvicorn redream_api.main:app --host 0.0.0.0 --port 8001
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api.router import router_v1
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
    yield


app = FastAPI(
    title="reDreamOSS server",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(RequestSizeLimitMiddleware)

app.include_router(router_v1, prefix="/api/v1")
