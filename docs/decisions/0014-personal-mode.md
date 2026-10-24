# 0014 — Personal mode: the same server for one person, on their computer

- Status: accepted
- Date: 2026-10-24
- Deciders: Daniel

## Context

ADR 0013 made reDreamOSS one self-hostable server with one app for every server. That still asks a lot of someone who only wants to use it for themselves: a Linux box, Docker, a domain or a tunnel, a signed APK, and the duties of an operator. The provider keys and the guide (prompts, crisis check, safety critic: Python) cannot move into the app, so a server process is needed; it does not need to be a separate machine.

## Decision

1. **`REGISTRATION_MODE=personal`.** The server has one local user and no sign-up. `POST /api/v1/auth/personal` gives that user a session only with `PERSONAL_KEY`: a random key `make start` keeps in `apps/server/data/personal.key` (owner-only) and hands to the browser in the address's fragment (`#key=…`, never sent to the server as part of the URL); the app keeps it and sends it as `X-Personal-Key`. The key is what keeps out anything that forwards to 127.0.0.1 without adding headers (`ssh -R`, socat, a plain `proxy_pass`) and other accounts on the same computer. On top, the request must come from this machine: a loopback client, a local `Host` (localhost, 127.0.0.1, [::1]; a DNS-rebinding page has its own) and no forwarding headers. The web app may not be framed by other sites (`frame-ancestors 'none'`). `/api/v1/instance` says `personal`; the app then signs in by itself and hides sign-in, sign-out, server change and the account's email and password.
2. **SQLite in personal mode.** `python -m redream_api.db.migrate` creates a SQLite file from the ORM models (which mirror the SQL migrations, ADR 0013) and keeps the newest migration's number in `PRAGMA user_version`; every later migration ships a SQLite step (`SQLITE_STEPS`, enforced by a test). Shared servers stay on Postgres.
3. **The web app, served by the server.** The Flutter app builds for the web again (Android and iOS stay); the server serves the build from `WEB_DIR` on the same origin as the API, so no CORS. On the web the page's origin is the server; microphone and playback use the browser, and there is no offline audio copy and no reminder.
4. **`make start`** (`scripts/start.py`, needs only uv): `.env` from the example on the first run; the web app of the checkout's newest release tag from GitHub Releases, checked against its SHA-256 (or built locally when Flutter is there); the schema; the server on 127.0.0.1:8000 in personal mode. The release workflow attaches the web build next to the APK.

## Consequences

### Positive
- One person needs uv, an API key and two commands; the data stays in one folder on their computer, seen only by their model provider.
- The same server code, prompts, crisis check and consent flow as a shared server; no second implementation.
- A shared server can serve the web app too (`WEB_DIR`), next to the APK.

### Negative
- Two databases to keep in step: a schema change needs its SQLite step as well.
- No phone in personal mode: a phone cannot reach the computer's localhost (that is the server + APK path).
- The web app depends on a release for `make start` without Flutter; a checkout between tags uses the last tag's web app (the app and server check their API version against each other).
- On the web, the browser keeps the session, the key and the app's local data without the platform keystore (the server is on the same machine anyway).
- A new browser needs the link with the key, which `make start` prints.

## Alternatives considered
- **Docker only (`docker compose up` with the web app):** needs Docker Desktop and a heavier first build; uv is a single binary.
- **Embedded Postgres (pgserver) instead of SQLite:** one schema path, but a bundled Postgres process to start, stop and recover; SQLite is a file.
- **The keys and the guide in the app:** the guide is Python (a port would drift from the evals), and keys in a distributed app are anyone's keys.
