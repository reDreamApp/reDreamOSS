# reDreamOSS

A self-help companion app for working with difficult dreams: record a dream, rewrite it with a new ending together with an AI guide, and rehearse it — by reading or by listening. You run the server; anyone you invite uses the app with it. *reDreamOSS does not diagnose or treat any condition. If nightmares are seriously distressing you, please talk to a doctor or psychotherapist.*

reDreamOSS is the open, self-hostable version of [reDreamAI](https://github.com/reDreamApp/reDreamAI): one small server instead of a hosted platform, any OpenAI-compatible model provider, and one Android app that works with every server ([ADR 0013](docs/decisions/0013-origin-of-redreamoss.md)).

## How it fits together

```
Android / iOS app ──HTTPS──► server (one container) ──► your model provider (OpenAI-compatible)
  asks for the server         login · chats · dreams       chat, crisis check, optional
  address once                consent · crisis check       speech-to-text and text-to-speech
                              rehearsal MP3s on a volume
                              Postgres (second container), optional Caddy for HTTPS
```

- **Run a server:** [docs/SELF_HOSTING.md](docs/SELF_HOSTING.md). A Linux box with Docker, a model provider's API key, about fifteen minutes.
- **Use the app:** install the APK from the [latest release](https://github.com/reDreamApp/reDreamOSS/releases/latest), enter the server address you were given, sign up with the invite code. Release builds only talk https.
- **Work on the code:** [docs/LOCAL_TESTING.md](docs/LOCAL_TESTING.md), then [RELEASING.md](RELEASING.md).

## Repository layout

```
apps/
  server/      Python FastAPI, one process: redream_api (users, chats, dreams, consent; source of truth)
               + redream_ai (guided chat, crisis check, speech), called in-process
    migrations/  the Postgres schema as numbered SQL files, applied by the server at start
  app/         Flutter (version pinned in apps/app/.fvmrc): Android + iOS client, EN + DE

compose.yaml   the whole stack on one box: Postgres + the server, optional Caddy (profile "caddy")
.env.example   every server setting, documented; scripts/init-env.sh turns it into .env
deploy/        Caddyfile for the caddy profile
scripts/       init-env.sh: creates .env with a database password and an invite code
docs/          self-hosting, local testing, roadmap, decisions/ (ADRs)
```

## Quick start (server)

```bash
git clone https://github.com/reDreamApp/reDreamOSS && cd reDreamOSS
sh scripts/init-env.sh     # .env with a database password and an invite code (printed)
# set LLM_BASE_URL, LLM_API_KEY, LLM_MODEL and LLM_SMALL_MODEL in .env
docker compose up -d --build --wait
```

The server listens on `127.0.0.1:8000`; put HTTPS in front of it (Caddy, a tunnel or your proxy) as [SELF_HOSTING.md](docs/SELF_HOSTING.md) explains, and read its section on your duties as the operator before you invite anyone.

## Development

```bash
make help                  # list everything
make db-migrate            # apply apps/server/migrations to DATABASE_URL (Postgres 16+; settings from .env)
make api-dev               # run the server locally on :8001
make app-run               # flutter run via FVM; the app asks for the server address on first start
make test                  # every test suite
```

## Decisions

Binding architectural decisions live in [`docs/decisions/`](docs/decisions/README.md). Key invariants:

- One plain Postgres (16+). Its schema is `apps/server/migrations/*.sql`, applied by `python -m redream_api.db.migrate` before the server starts; the ORM models must match it (CI checks).
- One server process; the AI side is stateless and in-process — every turn carries the full conversation.
- Structured output (JSON mode) for the dream summary. No string-matching of model prose.
- Self-help framing, never therapy (ADR 0004); `apps/app/test/wording_test.dart` checks every user-facing text.
- The crisis check runs before every guided turn; a flagged message gets the helpline text, not the model.
- Streaming chat end-to-end via SSE.
- Cost guard: invite-only sign-up by default and a per-user daily request cap (`DAILY_AI_REQUESTS_PER_USER`, default 100); set a spend limit at your provider too.

## License

Copyright 2026 Daniel Menzel, Katharina Lüth and Ramon Zacharias.

Licensed under the [Apache License 2.0](LICENSE); see [NOTICE](NOTICE). Report security problems privately: [SECURITY.md](SECURITY.md).
