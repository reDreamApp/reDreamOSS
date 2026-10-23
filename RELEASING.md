# Releasing

Decided 2026-07-02. Two long-lived branches, one release gate:

- **`main`** — development. Any Postgres 16+ serves as the dev database
  (`make db-migrate` applies `apps/server/migrations/` to `DATABASE_URL`).
  CI's `postgres-schema` job proves every push applies the migrations cleanly
  from zero on Postgres 17 and that they match the ORM models; its `stack`
  job builds and starts `compose.yaml` and logs in against it. Pushing main
  never deploys anything.
- **`production`** — the release gate: what operators run. Nothing deploys
  automatically; on the box, an operator updates with
  `git pull` (on `production`) and `make update` (`docker compose pull
  --ignore-buildable`, `docker compose build --pull`, `docker compose up -d
  --wait`: without the pulls, Postgres, Caddy and the Python base image stay
  on whatever version the box first fetched). The server
  container **applies pending migrations before it starts**
  (`python -m redream_api.db.migrate`; a failed migration stops it). Code and
  schema travel in the same image, so the API can never start against a
  database missing its migration.

## How a schema change ships

1. Add the next numbered file, `apps/server/migrations/000N_feature_x.sql`
   (plain SQL without BEGIN/COMMIT: the runner applies each file and its
   `schema_migrations` row in one transaction), and change the ORM models to
   match, in the same commit. Never edit a file that has been applied
   anywhere: the runner refuses one whose checksum changed.
2. Check locally: `make db-migrate`, then `make db-test` with
   `TEST_DATABASE_URL` pointing at an empty, throwaway database (runner,
   schema drift against the ORM, smoke test on the migrated schema).
3. Merge to `main` → CI's `postgres-schema` job re-proves both from zero.
4. Release: fast-forward `production` to main and push:
   `git push origin main:production`
   → on each box, `git pull` and `make update` rebuild the server; it
   migrates the database as it starts.

## Consent-text change

The consent version is a code constant on each side:
`redream_api.core.consent.CONSENT_VERSION` (server) and `consentTextVersion`
in `apps/app/lib/features/consent/consent_service.dart` (app); a test keeps
them equal. Bump both together, and only on a material change to the consent
copy (a new processor or purpose): every user then consents again. **Deploy
the server before the app**: an old server refuses grants from an app that
sends the new version.

## One-time setup

- A box: see "Run it" in [README.md](README.md) (`scripts/init-env.sh`,
  `docker compose up -d --build --wait`, the first user with the CLI). Updates need
  no database access of their own: the server container migrates the
  database it is configured with.
- [ ] Create the branch: `git branch production && git push origin production`.

## Caveats

- The default `apps/server` tests build their tables from the ORM models on
  SQLite, so on their own they never see a model change without its
  migration (or the reverse). `tests/pg` (CI's `postgres-schema` job) does:
  it compares the migrated Postgres with the models. Both belong in the same
  commit.
