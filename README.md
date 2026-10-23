# reDreamAI

A self-help companion app for working with difficult dreams: record a dream, reimagine it with a new ending, and listen back to it. *reDreamAI does not diagnose or treat any condition. If nightmares are seriously distressing you, please talk to a doctor or psychotherapist.*

## Repository layout

```
apps/
  server/    Python FastAPI, one process: redream_api (async SQLAlchemy, source of truth for users, chats, dreams) + redream_ai (chat, safety classifier, TTS, STT), called in-process
    migrations/  the Postgres schema as numbered SQL files, applied by the server container at start
  app/       Flutter (version pinned in apps/app/.fvmrc): iOS + Android client, EN + DE

compose.yaml    the whole stack on one box: Postgres + the server, optional Caddy (profile "caddy")
.env.example    every server setting, documented; scripts/init-env.sh turns it into .env
deploy/
  Caddyfile     HTTPS reverse proxy for the caddy profile
scripts/
  init-env.sh   creates .env with a database password and an invite code

docs/
  decisions/    Architecture Decision Records (binding)
```

## Runtime flow

```
Flutter app
   ↓ HTTPS + SSE (Caddy)
apps/server  redream_api ← source of truth (chats, dreams, safety events, daily mood)
   ↓ in-process
             redream_ai  ← stateless: receives full history, returns next IRT stage + response
   ↓
Scaleway Generative APIs (chat, speech-to-text), Mistral (fallback, text-to-speech)
```

## Run it

The server and its Postgres run with Docker Compose on one small box:

```bash
sh scripts/init-env.sh     # .env from .env.example, with a database password and an invite code
# fill in LLM_BASE_URL, LLM_API_KEY, LLM_MODEL and LLM_SMALL_MODEL in .env
docker compose up -d --build --wait
docker compose exec server python -m redream_api.cli create-user you@example.org
```

The server then listens on `127.0.0.1:8000` only (`SERVER_PORT` changes the
port): reach it through an HTTP tunnel or your own reverse proxy that sends
`X-Forwarded-For` (set or appended; the rate limits count the address in it,
and without it they count every client as one).
For HTTPS straight from the box, set `DOMAIN` and `COMPOSE_PROFILES=caddy` in
`.env` (DNS pointing straight at the box, ports 80 and 443 open) and run the
same `docker compose up -d --build --wait`: Caddy starts with it. People sign
up in the app with the invite code `init-env.sh` printed (`REGISTRATION_MODE`,
`INVITE_CODE`); every setting is documented in [`.env.example`](.env.example).

Update with `git pull` and `make update` (pulls newer `postgres:17` and
`caddy:2` images, rebuilds the server on a fresh base image, restarts; the
server migrates the database as it starts). Back up the database with
`docker compose exec -T db pg_dump -U redream redream > redream.sql`; the
rehearsal MP3s are in the `redream_audio` volume. `make init-env`, `make up`,
`make down`, `make logs` and `make create-user EMAIL=…` are shortcuts.

## Development

```bash
cp apps/app/.env.example apps/app/.env   # the app bundles .env as an asset; needed before build/test
make help                  # list everything
make db-migrate            # apply apps/server/migrations to DATABASE_URL (Postgres 16+; settings from .env)
make api-dev               # run the server (backend + AI) on :8001
make app-run               # flutter run via FVM
make test                  # all test suites
```

## Decisions

Binding architectural decisions live in [`docs/decisions/`](docs/decisions/README.md). Read those before making structural changes.

Key invariants:
- One plain Postgres (16+). Its schema is `apps/server/migrations/*.sql`, applied by `python -m redream_api.db.migrate` before the server starts; the ORM models must match it (CI checks).
- AI service is stateless — every request carries the full conversation history.
- Structured outputs (JSON mode) for the summary. No string-matching parsers.
- Self-help framing, never therapy. Disclaimer copy required on user-facing surfaces.
- Crisis detection runs before every IRT turn; high-risk input bypasses the LLM.
- 100k LLM tokens / user / day, 500k / day for all users together (UTC reset).
- Streaming chat end-to-end via SSE.
- Pinned model IDs — no floating aliases (one recorded exception, ADR 0010).

## License

Copyright 2026 Daniel Menzel, Katharina Lüth and Ramon Zacharias.

Licensed under the [Apache License 2.0](LICENSE); see [NOTICE](NOTICE).
