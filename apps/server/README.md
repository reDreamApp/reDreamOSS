# apps/server — backend + AI

One Python project with two packages side by side: `redream_api` (FastAPI + async SQLAlchemy + Supabase JWT; source of truth for users, dreams, chats, daily moods) and `redream_ai` (IRT chatbot, crisis classifier, Voxtral STT/TTS). Imports only go `redream_api` → `redream_ai`, never the other way. One process: the backend calls the AI side in-process (`redream_api.services.ai_client`); no AI route is reachable over HTTP.

## Local dev

```bash
cp .env.example .env       # fill in DATABASE_URL, Supabase, API keys
uv sync
make api-dev               # from repo root: server on :8001 (uv run --env-file .env, if present)
```

`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` and `LLM_SMALL_MODEL` are required for chat turns and the crisis check: any OpenAI-compatible chat-completions endpoint, one provider for every text role (examples in `.env.example`). They come from the environment or `.env`; environment variables win.

## Tests

```bash
uv run pytest              # or `make ai-test` / `make api-test` from repo root
```

Fully offline: in-memory SQLite (aiosqlite) with a `FakeAiClient` for `tests/api`, `FakeLlmClient` + monkeypatched core agents for `tests/ai`. No live Supabase, no live LLM calls, no API keys. `evals/` holds the live regression evals (real providers, not part of pytest).

## Architecture

- `redream_api.api.v1` — feature packages, mounted at `/api/v1` (one folder each: `routes.py` + `schemas.py` + optional `service.py`).
- `redream_api.db` — ORM models (one file per table, mirrors the Supabase migration) and repositories. **Routes go through repos, never raw model queries.**
- `redream_api.services` — AI client (in-process, maps every AI failure to `AIServiceError`), Supabase Auth admin + Storage deletes (account deletion, GDPR purge).
- `redream_ai.api` — the plain async functions the AI client calls (`chat`, `chat_stream`, `safety_classify`, `transcribe`, `rehearsal_generate`).
- `redream_ai.core` / `redream_ai.clinical_safety` — vendored IRT engine + `SafetyCritic` (excluded from lint/type rules). Chat/routing/safety-critic models come from `LLM_MODEL` / `LLM_SMALL_MODEL`.
- `redream_ai.safety.classifier` — crisis classifier on `LLM_SAFETY_MODEL` (default `LLM_SMALL_MODEL`), no fallback; a failed check fails the chat turn.
- `redream_ai.audio` — Voxtral TTS + Supabase Storage upload, Whisper/Voxtral STT.

See [`../../docs/decisions/`](../../docs/decisions/README.md) for the binding architectural decisions.
