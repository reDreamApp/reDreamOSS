# Local testing

How to run the server and the app on your own machine, and which tests cover what.

## The offline suites (no keys, no database)

```bash
make test        # server (tests/ai + tests/api on SQLite), deploy checks, Flutter tests
make lint        # ruff + mypy, deploy-check lint, flutter analyze (redream_api has a few known
                 # lint findings, informational in CI: run the other targets on their own)
```

- `apps/server/tests/api` and `tests/ai` run on in-memory SQLite with fake AI and LLM clients: no network, no API keys.
- `apps/app/test` drives real widgets against an in-memory fake server (`test/fake_api.dart`), including the address screen, sign-in, consent, chat streaming and the journal. `test/wording_test.dart` checks every user-facing text against the self-help framing.
- `apps/server/tests/deploy` checks `compose.yaml`, `.env.example` and `scripts/init-env.sh` without Docker.

Run Flutter tests with `--concurrency=2` on a laptop; the default starts one tester per core.

## The server against a real Postgres

Any Postgres 16+ works (a local install, `docker run postgres:17`, or the compose stack's `db` service).

```bash
make init-env                      # .env at the repo root, from .env.example
# in .env: DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@localhost:5432/DB and the LLM_* values
make db-migrate                    # applies apps/server/migrations
make api-dev                       # the server on http://localhost:8001, reloads on code changes
make create-user EMAIL=you@example.org   # only for the compose stack; locally:
cd apps/server && uv run --env-file ../../.env python -m redream_api.cli create-user you@example.org
```

`make db-test` runs `tests/pg` (the migration runner, schema drift against the ORM models, a smoke test from sign-up to account deletion) against `TEST_DATABASE_URL`, which must be an empty, throwaway database. CI does the same on Postgres 17 and also builds and starts the whole compose stack.

Without `LLM_*` values the server still starts (`/api/v1/health` reports `llm_ready: false`): sign-up, consent and the journal work, chat turns fail with an error.

## The app against your local server

```bash
cd apps/app
fvm flutter run          # on an emulator or a device
```

On first start the app asks for the server address. Type the scheme: an address without one is taken as `https://`.

| Where the app runs | Address |
|---|---|
| Android emulator | `http://10.0.2.2:8001` |
| iOS simulator | `http://localhost:8001` |
| A phone on your Wi-Fi | `http://<your computer's LAN address>:8001`, with the server listening on all interfaces: `cd apps/server && uv run --env-file ../../.env uvicorn redream_api.main:app --host 0.0.0.0 --port 8001` |

Debug builds accept `http://`; release builds only `https://`, except for `localhost`, `127.0.0.1` and `::1`. On iOS, streaming rehearsal audio from an `http://` server that is neither `localhost` nor an IP address can fail under App Transport Security; "Save offline" still works. `--dart-define=DEFAULT_SERVER_URL=http://10.0.2.2:8001` presets the address and connects on a fresh install.

## The web app and personal mode

`make start` runs personal mode as people use it (README). While working on the app, build the web app from your checkout instead of downloading the release's:

```bash
uv run --project apps/server python scripts/start.py --build-web     # flutter build web, then the server on :8000
```

The web app talks to the server it was loaded from, so `fvm flutter run -d chrome` against `make api-dev` (another origin, no CORS) does not work: build it and let the server serve it (`WEB_DIR`). Personal mode keeps its SQLite file, audio, web build and sign-in key (`personal.key`) in `apps/server/data/`; delete that folder for a fresh start. The browser signs in with the key from the address (`http://localhost:8000/#key=…`, printed at start); to call the API yourself: `curl -X POST -H "X-Personal-Key: $(cat apps/server/data/personal.key)" http://localhost:8000/api/v1/auth/personal`.

## The live smoke test

`apps/app/integration_test/live_smoke_test.dart` drives the real app against a real server: sign-in, consent, one chat turn with a streamed reply. It calls your model provider, so it is not part of CI.

```bash
cd apps/app
fvm flutter test integration_test/live_smoke_test.dart -d <device> \
  --dart-define=DEFAULT_SERVER_URL=http://10.0.2.2:8001 \
  --dart-define=SMOKE_EMAIL=you@example.org --dart-define=SMOKE_PASSWORD=...
```

## Live checks of the prompts

Prompt or model changes are checked against the real provider with the two evals in `apps/server/evals` (see [SELF_HOSTING.md](SELF_HOSTING.md#models)): the crisis check on fixed cases, and the rewriting stage's opener and the summary→final routing.
