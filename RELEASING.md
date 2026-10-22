# Releasing

Decided 2026-07-02. Two long-lived branches, one release gate:

- **`main`** — development. The dev database is the **local Supabase stack**
  (`supabase start`, see docs/LOCAL_TESTING.md); CI's `migrations` job proves
  every push applies `supabase/migrations/` cleanly from zero. Pushing main
  never deploys anything.
- **`production`** — the release gate. Pushing it runs
  `.github/workflows/deploy.yml`, which rsyncs the repo to the Scaleway host,
  **applies pending migrations to the self-hosted Postgres first**, then
  `docker compose up -d --build`. Code and schema travel on the same ref, so
  the API can never start against a database missing its migration.

## How a schema change ships

1. Create it: `make db-new-migration NAME=feature_x`, edit the SQL, test
   locally against the local stack (`supabase migration up --local`, or
   `supabase db reset` for a from-zero check).
2. Merge to `main` → CI's `migrations` job re-proves the full chain applies
   from zero on the Supabase Postgres image.
3. Release: fast-forward `production` to main and push:
   `git push origin main:production`
   → migrations apply to the self-hosted DB, then containers rebuild.

## One-time setup

- The box: `infra/bootstrap.sh` installs the Supabase CLI and writes
  `/srv/redreamai/infra/env/deploy.env` with
  `SUPABASE_DB_URL=postgresql://postgres:<password>@127.0.0.1:54322/postgres?sslmode=disable`
  (host-only file; `infra/env/` is excluded from rsync and git). Port 54322 is
  Postgres on loopback, published by `infra/supabase/docker-compose.redream.yml`.
  The CLI needs the connection string **percent-encoded**; the generated
  password is hex, so that only matters if you change it by hand.
- GitHub: the four deploy secrets (`SCALEWAY_HOST`, `SCALEWAY_PORT`,
  `SCALEWAY_USER`, `SCALEWAY_SSH_KEY`; see infra/README.md).
- [ ] Create the branch: `git branch production && git push origin production`.

## Caveats

- First-ever deploy: the workflow needs a bootstrapped box (infra/README.md,
  "First deploy") — Supabase running and `deploy.env` in place.
- Auth (`auth.*`) is Supabase-managed and works regardless of branch wiring;
  only `public.*` schema depends on migrations reaching the DB.
- `apps/server` tests don't need any of this (in-memory SQLite from the ORM
  models) — which is exactly why a missing migration surfaces only in a real
  environment. When you add a column to a model, the migration belongs in the
  same commit.
