"""Health endpoint sanity check (also boots the FastAPI app)."""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from redream_api.main import app


async def test_health_returns_ok() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
