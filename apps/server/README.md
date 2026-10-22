# apps/server — backend + AI

One Python project with two packages side by side: `redream_api` (FastAPI + async SQLAlchemy + Supabase JWT; source of truth for users, dreams, chats, daily moods) and `redream_ai` (IRT chatbot, crisis classifier, Voxtral STT/TTS). Imports only go `redream_api` → `redream_ai`, never the other way. One process: the backend calls the AI side in-process (`redream_api.services.ai_client`); no AI route is reachable over HTTP.

## Local dev

```bash
cp .env.example .env       # fill in DATABASE_URL, Supabase, API keys
uv sync
make api-dev               # from repo root: server on :8001 (uv run --env-file .env, if present)
```

`SCALEWAY_API_KEY` **and** `MISTRAL_API_KEY` are both required for chat turns (Scaleway primary, direct-Mistral fallback). The IRT core reads them from the process environment only, which is why `api-dev` passes `--env-file .env` when the file exists; the app never loads `.env` into it.

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
- `redream_ai.core` / `redream_ai.clinical_safety` — vendored IRT engine + `SafetyCritic` (excluded from lint/type rules). Chat/routing/safety-critic model IDs are pinned in `core/agent.py::MODELS` (ADR 0010).
- `redream_ai.safety.classifier` — JSON-mode crisis classifier: Mistral Small 3.2 on Scaleway, Ministral 8B on direct Mistral as fallback.
- `redream_ai.audio` — Voxtral TTS + Supabase Storage upload, Whisper/Voxtral STT.

See [`../../docs/decisions/`](../../docs/decisions/README.md) for the binding architectural decisions.
