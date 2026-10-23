# apps/server — backend + AI

One Python project with two packages side by side: `redream_api` (FastAPI + async SQLAlchemy + its own email/password login with opaque bearer tokens; source of truth for users, dreams, chats, daily moods) and `redream_ai` (IRT chatbot, crisis classifier, STT/TTS). Imports only go `redream_api` → `redream_ai`, never the other way. One process: the backend calls the AI side in-process (`redream_api.services.ai_client`); no AI route is reachable over HTTP.

## Local dev

```bash
cp .env.example .env       # fill in DATABASE_URL, INVITE_CODE, API keys
uv sync
make db-migrate            # from repo root: apply migrations/ to DATABASE_URL
make api-dev               # from repo root: server on :8001 (uv run --env-file .env, if present)
```

The database is any Postgres 16+. Its schema is `migrations/*.sql`, applied in name order by `python -m redream_api.db.migrate` (locally `uv run --env-file .env python -m redream_api.db.migrate`; the container runs it before uvicorn). It needs only `DATABASE_URL`, from the environment (not `.env`), read as the app's engine reads it (`?ssl=require` and the other SQLAlchemy asyncpg options apply to both), over a direct or session-mode connection (it holds an advisory lock for the run; not a transaction pooler). It waits up to 30 s for the database, records each file with its sha256 in `schema_migrations`, and refuses to continue if an applied file has changed: a schema change is a new numbered file (plain SQL, no BEGIN/COMMIT: each file runs in the runner's own transaction) plus the matching ORM change.

`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` and `LLM_SMALL_MODEL` are required for chat turns and the crisis check: any OpenAI-compatible chat-completions endpoint, one provider for every text role (examples in `.env.example`). They come from the environment or `.env`; environment variables win.

Accounts: `REGISTRATION_MODE` is `invite` by default, and then `INVITE_CODE` (8 to 256 characters) is required or the server does not start; `open` and `closed` are the alternatives. The operator CLI creates users and resets passwords in every mode: `uv run python -m redream_api.cli create-user EMAIL` / `set-password EMAIL` (the password is asked for, or read from stdin with `--password-stdin`). `DAILY_AI_REQUESTS_PER_USER` (default 100, 0 = off) caps chat turns, transcriptions and rehearsal renderings per user per UTC day, in memory.

`GET /api/v1/instance` (no login) tells the app what this server is: `INSTANCE_NAME`, the operator (`OPERATOR_NAME`, `OPERATOR_CONTACT`, `PRIVACY_URL`, all optional), the API and consent versions, registration mode, daily cap and which features are configured (chat, transcription, rehearsal audio).

`GET /api/v1/health` answers 200 `{"status": "ok"}` whenever the process is up, with `llm_ready` (the four `LLM_*` settings are set) and `safety_classifier_enabled`.

The consent-text version is a code constant (`redream_api.core.consent.CONSENT_VERSION`), not a setting: it must equal the app's `consentTextVersion`.

`HELPLINE_TEXT_EN` / `HELPLINE_TEXT_DE` (the message the app shows when the crisis check flags a message) and `HELPLINE_PROMPT_EN` / `HELPLINE_PROMPT_DE` (the short help reference the chat model names on real distress) are optional; blank keeps a neutral default without country-specific numbers (limits and a German example in `.env.example`).

A chat can switch between English and German mid-chat: before the crisis check, each message passes a cheap word check and, if it looks like (or asks for) the other language, one short `LLM_SMALL_MODEL` call; on a switch the chat continues in that language (crisis check, helpline text and reply included). Any failure keeps the language. A dream keeps the chat's language at the summary, and its rehearsal audio uses it.

## Tests

```bash
uv run pytest              # or `make ai-test` / `make api-test` from repo root
```

Fully offline: in-memory SQLite (aiosqlite) with a `FakeAiClient` for `tests/api`, `FakeLlmClient` + monkeypatched core agents for `tests/ai`. No live LLM calls, no API keys. `evals/` holds the live regression evals (real providers, not part of pytest).

`tests/pg` runs on a real Postgres and is skipped unless `TEST_DATABASE_URL` is set (`postgresql+asyncpg://…` to an empty, throwaway database; `make db-test`): the migration runner, a drift check of the migrated schema against the ORM models (every table, column, default, key, index, CHECK, trigger and function, as Postgres renders them, against `create_all`'s) and a smoke test of the app from registration to account deletion.

## Architecture

- `redream_api.api.v1` — feature packages, mounted at `/api/v1` (one folder each: `routes.py` + `schemas.py` + optional `service.py`).
- `redream_api.db` — ORM models (one file per table; they must match `migrations/`, which `tests/pg` checks), repositories and the migration runner (`migrate.py`). **Routes go through repos, never raw model queries.**
- `redream_api.services` — AI client (in-process, maps every AI failure to `AIServiceError`), accounts (email rules, users, passwords — shared by the auth routes and the CLI), rehearsal-audio links (short-lived HMAC-signed, served by `GET /api/v1/audio/files/{key}`) and folder deletes (dream and account deletion).
- `redream_api.core` — settings, dependencies (`get_current_user`: bearer token → its sha256 → `auth_tokens` row, 30 days sliding), argon2id passwords, and the in-memory rate limits and daily AI-request cap (`limits.py`; one worker, so one set of counters).
- `redream_api.cli` — the operator CLI (`create-user`, `set-password`).
- `redream_ai.api` — the plain async functions the AI client calls (`chat`, `chat_stream`, `safety_classify`, `language_check`, `transcribe`, `rehearsal_generate`).
- `redream_ai.core` / `redream_ai.clinical_safety` — vendored IRT engine + `SafetyCritic` (excluded from lint/type rules). Chat/routing/safety-critic models come from `LLM_MODEL` / `LLM_SMALL_MODEL`.
- `redream_ai.safety.classifier` — crisis classifier on `LLM_SAFETY_MODEL` (default `LLM_SMALL_MODEL`), no fallback; a failed check fails the chat turn.
- `redream_ai.audio` — TTS + the local MP3 store under `AUDIO_DIR`, STT; one provider each from `STT_*` / `TTS_*` (see `.env.example`), off without a model (the routes answer 503 `voice_unavailable`).

See [`../../docs/decisions/`](../../docs/decisions/README.md) for the binding architectural decisions.
