# apps/api — backend

FastAPI + async SQLAlchemy + Supabase JWT. Source of truth for users, dreams, chats, reports, daily moods, safety events, daily token usage. Calls `apps/ai` for IRT routing, safety classification, TTS, STT.

## Endpoints

Mounted at `/api/v1`:

| Feature | Routes |
|---|---|
| `health` | `GET /health` |
| `auth` | `POST /auth/session/validate`, `POST /auth/password-reset` |
| `users` | `DELETE /users/me`, `DELETE /users/me/data` (GDPR) |
| `profile` | `GET/POST /profile`, `PATCH /profile/nightmare-frequency` |
| `dreams` | full CRUD, `PATCH /favorite`, `POST /practice`, `POST /rehearsal` |
| `chats` | `POST /chats/start`, `GET /chats`, `GET /chats/{id}/messages`, `POST /chats/{id}/message` (calls AI safety + chat), `DELETE /chats/{id}` |
| `progress` | `GET /progress/stats`, `POST /progress/emotion` |
| `reports` | `POST /reports/ai-content` (rate-limited; sends email notification — ADR 0006) |
| `exports` | `GET /exports/data` (JSON dump for the user) |
| `help_center` | `POST /help-center/concerns` |

Internal at `/internal` (gated by `REPORT_INTERNAL_ACTION_KEY` query param + IP allowlist):
- `GET /internal/reports/{id}/resolve`
- `GET /internal/reports/{id}/reject`
- `GET /internal/messages/{id}/hide`

**No Telegram** webhook (ADR 0006 — replaced by email + HMAC-signed links above).

## Local dev

```bash
cp .env.example .env       # fill in DATABASE_URL, Supabase, AI_SERVICE_URL
uv sync
make api-dev               # from repo root, or:
uv run uvicorn redream_api.main:app --reload --port 8001
```

## Tests

```bash
make api-test
```

Tests run against in-memory SQLite (aiosqlite) with a `FakeAiClient` — no live Supabase, no live AI service.

## Architecture

- `redream_api.core` — config, JWT verification, dependency injection, logging.
- `redream_api.db.models` — SQLAlchemy ORM models, one file per table (mirrors the Supabase migration).
- `redream_api.db.repositories` — async data-access wrappers. **Routes go through repos, never raw model queries.**
- `redream_api.services.ai_client` — outbound calls to the AI service. Handles structured-output for the summary stage (no `_parse_summary` string matching — ADR 0003).
- `redream_api.services.email_notification` — moderation alerts via aiosmtplib + HMAC-signed action links (ADR 0006).
- `redream_api.services.storage_service` — Supabase Storage delete (used by GDPR purge).
- `redream_api.services.langfuse_service` — actual Langfuse trace deletion on user purge (replaces legacy no-op stub — ADR 0007).
- `redream_api.api.v1` — feature packages (one folder each: `routes.py` + `schemas.py` + optional `service.py`).
- `redream_api.api.internal` — moderation actions reachable only via key + allowlist.

See [`../../docs/decisions/`](../../docs/decisions/README.md) for binding architectural decisions.
