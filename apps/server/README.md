# apps/server — backend + AI

One Python project with two packages side by side: `redream_api` (FastAPI + async SQLAlchemy + its own email/password login with opaque bearer tokens; source of truth for users, dreams, chats, daily moods) and `redream_ai` (IRT chatbot, crisis classifier, STT/TTS). Imports only go `redream_api` → `redream_ai`, never the other way. One process: the backend calls the AI side in-process (`redream_api.services.ai_client`); no AI route is reachable over HTTP.

## Local dev

```bash
cp .env.example .env       # fill in DATABASE_URL, INVITE_CODE, API keys
uv sync
make api-dev               # from repo root: server on :8001 (uv run --env-file .env, if present)
```

`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` and `LLM_SMALL_MODEL` are required for chat turns and the crisis check: any OpenAI-compatible chat-completions endpoint, one provider for every text role (examples in `.env.example`). They come from the environment or `.env`; environment variables win.

Accounts: `REGISTRATION_MODE` is `invite` by default, and then `INVITE_CODE` (8 to 256 characters) is required or the server does not start; `open` and `closed` are the alternatives. The operator CLI creates users and resets passwords in every mode: `uv run python -m redream_api.cli create-user EMAIL` / `set-password EMAIL` (the password is asked for, or read from stdin with `--password-stdin`). `DAILY_AI_REQUESTS_PER_USER` (default 100, 0 = off) caps chat turns, transcriptions and rehearsal renderings per user per UTC day, in memory.

`HELPLINE_TEXT_EN` / `HELPLINE_TEXT_DE` (the message the app shows when the crisis check flags a message) and `HELPLINE_PROMPT_EN` / `HELPLINE_PROMPT_DE` (the short help reference the chat model names on real distress) are optional; blank keeps a neutral default without country-specific numbers (limits and a German example in `.env.example`).

## Tests

```bash
uv run pytest              # or `make ai-test` / `make api-test` from repo root
```

Fully offline: in-memory SQLite (aiosqlite) with a `FakeAiClient` for `tests/api`, `FakeLlmClient` + monkeypatched core agents for `tests/ai`. No live LLM calls, no API keys. `evals/` holds the live regression evals (real providers, not part of pytest).

## Architecture

- `redream_api.api.v1` — feature packages, mounted at `/api/v1` (one folder each: `routes.py` + `schemas.py` + optional `service.py`).
- `redream_api.db` — ORM models (one file per table; the source of truth for the schema) and repositories. **Routes go through repos, never raw model queries.**
- `redream_api.services` — AI client (in-process, maps every AI failure to `AIServiceError`), accounts (email rules, users, passwords — shared by the auth routes and the CLI), rehearsal-audio links (short-lived HMAC-signed, served by `GET /api/v1/audio/files/{key}`) and folder deletes (dream and account deletion).
- `redream_api.core` — settings, dependencies (`get_current_user`: bearer token → its sha256 → `auth_tokens` row, 30 days sliding), argon2id passwords, and the in-memory rate limits and daily AI-request cap (`limits.py`; one worker, so one set of counters).
- `redream_api.cli` — the operator CLI (`create-user`, `set-password`).
- `redream_ai.api` — the plain async functions the AI client calls (`chat`, `chat_stream`, `safety_classify`, `transcribe`, `rehearsal_generate`).
- `redream_ai.core` / `redream_ai.clinical_safety` — vendored IRT engine + `SafetyCritic` (excluded from lint/type rules). Chat/routing/safety-critic models come from `LLM_MODEL` / `LLM_SMALL_MODEL`.
- `redream_ai.safety.classifier` — crisis classifier on `LLM_SAFETY_MODEL` (default `LLM_SMALL_MODEL`), no fallback; a failed check fails the chat turn.
- `redream_ai.audio` — TTS + the local MP3 store under `AUDIO_DIR`, STT; one provider each from `STT_*` / `TTS_*` (see `.env.example`), off without a model (the routes answer 503 `voice_unavailable`).

See [`../../docs/decisions/`](../../docs/decisions/README.md) for the binding architectural decisions.
