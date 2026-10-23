# Production deployment (Scaleway)

One Scaleway instance runs everything: the official self-hosted Supabase stack
and, next to it, this repo's compose stack. The compose file here does **not**
include Postgres — Supabase owns it (ADR 0001).

## Topology

```
Scaleway instance (GP1-XS, Ubuntu 24.04 — infra/terraform)
├── /srv/supabase    official self-hosted Supabase (Postgres 17, Auth, Storage, …)
│                    nothing published except on loopback: gateway :8000, Postgres :54322
└── /srv/redreamai   this repo — network: default + Supabase's `supabase_default`
    ├── caddy        TLS via Let's Encrypt; the only thing on public ports (80 / 443)
    ├── api          apps/api — backend, talks to supabase-db and ai
    ├── ai           apps/ai — stateless AI service, internal only
    └── langfuse     self-hosted v2 (ADR 0007), database in the same Postgres
```

What Caddy lets in (`caddy/Caddyfile`):

| Host | Goes to | Notes |
|---|---|---|
| `DOMAIN` | api | the app's `API_BASE_URL` |
| `supabase.DOMAIN` | Supabase gateway | **only** `/auth/v1/*` and `/storage/v1/object/sign/*`; the app's `SUPABASE_URL` |
| `langfuse.DOMAIN` | langfuse | sign-up disabled |

PostgREST, GraphQL, Realtime, Functions, Studio and the AI service are not
reachable from outside.

## First deploy

Three steps. The whole path was run end to end on 2026-10-03.

### 1. Create the box

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars   # project_id + SSH public key(s)
tofu init && tofu apply
```

Creates the instance (Docker installed on first boot), a reserved IP, the
firewall, the restricted runtime API key and the backup bucket. Details and
costs: [`terraform/README.md`](terraform/README.md).

### 2. Put the repo on the box

On the box, `git clone <repo-url> /srv/redreamai`. Or push your checkout's
committed tree from the laptop:

```bash
git archive HEAD | ssh root@<server_ip> 'mkdir -p /srv/redreamai && tar -x -C /srv/redreamai'
```

### 3. Run the bootstrap

On the box, write the inputs to `/srv/redreamai/infra/env/bootstrap.env`
(`chmod 600`), then run the script:

```bash
# /srv/redreamai/infra/env/bootstrap.env
DOMAIN=              # your domain, or `tofu output -raw test_domain` (needs no DNS)
SCALEWAY_API_KEY=    # tofu output -raw runtime_secret_key
SCALEWAY_PROJECT_ID= # tofu output -raw project_id
MISTRAL_API_KEY=     # Mistral La Plateforme key (fallback models, text-to-speech)
BACKUP_ACCESS_KEY=   # tofu output -raw runtime_access_key
BACKUP_BUCKET=       # tofu output -raw backup_bucket_name
```

```bash
bash /srv/redreamai/infra/bootstrap.sh
```

[`bootstrap.sh`](bootstrap.sh) takes about ten minutes on a fresh box and:

- installs Supabase with **Supabase's own installer** (`setup.sh`, pinned
  release): compose files, every secret, the ES256 signing key, the AWS CLI;
- layers [`supabase/docker-compose.redream.yml`](supabase/docker-compose.redream.yml)
  on top, which takes Supabase off the public interfaces;
- generates `infra/env/*.env` from `env.example/` (secrets are created on the
  box and never printed);
- creates the Langfuse database, with an admin user and an API key pair
  provisioned on first start;
- applies `supabase/migrations/`, builds and starts the stack, enables the
  nightly backup and the daily trace retention.

It is safe to run again: existing secrets are kept, URLs and keys from
`bootstrap.env` are re-applied. That is also how you **change the domain**:
edit `DOMAIN` in `bootstrap.env`, point DNS, run it again.

With your own domain, `DOMAIN`, `supabase.DOMAIN` and `langfuse.DOMAIN` need A
records to `tofu output server_ip` before the run, or Caddy cannot get
certificates.

### The app's `.env`

```
SUPABASE_URL=https://supabase.<DOMAIN>
API_BASE_URL=https://<DOMAIN>
SUPABASE_ANON_KEY=<ANON_KEY from /srv/supabase/.env on the box>
```

### Things to know

- **Mail.** No SMTP is configured, and Scaleway blocks outbound ports
  25/465/587 by default. So sign-up confirms addresses automatically
  (`ENABLE_EMAIL_AUTOCONFIRM=true` in `/srv/supabase/.env`), and password-reset
  and moderation mails are not delivered until you configure an SMTP relay
  there and in `infra/env/api.env`.
- **Scaleway project.** A key restricted to one project gets `403` on
  `https://api.scaleway.ai/v1`: that URL means the organization's default
  project. `SCALEWAY_PROJECT_ID` puts the project into the URL.
- **Langfuse sign-up.** `AUTH_DISABLE_SIGNUP=true` is what closes it;
  `NEXT_PUBLIC_SIGN_UP_DISABLED` alone does not (checked on v2.95.11).
- **Langfuse and account deletion.** Langfuse v2 cannot delete traces through
  its API, so apps/api deletes a user's traces in the `langfuse` database
  (`LANGFUSE_DATABASE_URL` in `api.env`, ADR 0007).
- **SSH keys.** The box rebuilds `/root/.ssh/authorized_keys` at every boot from
  the Scaleway project's SSH keys. Add keys through `ssh_public_keys` in
  terraform, then run `scw-fetch-ssh-keys --upgrade` on the box.
- **Scanners.** Expect 404/502 noise in the Caddy log within minutes: new
  certificates are public in the certificate-transparency logs.

## Deploy a new version

Push the `production` branch (`git push origin main:production`), or run
**Deploy to Scaleway** by hand from the Actions tab. The workflow rsyncs a clean
checkout to `/srv/redreamai/`, applies pending migrations
(`supabase db push` with `SUPABASE_DB_URL` from the host-only
`infra/env/deploy.env`), then runs `docker compose up -d --build` on the box.
Pushing `main` never deploys. More in [`RELEASING.md`](../RELEASING.md).

### Required GitHub secrets

| Secret | What it is |
|---|---|
| `SCALEWAY_HOST`     | `tofu output -raw server_ip` |
| `SCALEWAY_PORT`     | SSH port (`22`) |
| `SCALEWAY_USER`     | SSH user (`root`) |
| `SCALEWAY_SSH_KEY`  | Private half of a dedicated deploy key; its public half goes into `ssh_public_keys` |

## Looking inside

- **Logs**: `cd /srv/redreamai/infra && docker compose logs -f api ai caddy`
- **Supabase**: `cd /srv/supabase && sh run.sh status` (its own helper; also
  `logs`, `restart`, `secrets`)
- **Supabase Studio**: `ssh -L 8000:127.0.0.1:8000 root@<server_ip>`, then
  `http://localhost:8000` (user and password: `DASHBOARD_USERNAME` /
  `DASHBOARD_PASSWORD` in `/srv/supabase/.env`)
- **Langfuse**: `https://langfuse.<DOMAIN>` (login: `LANGFUSE_INIT_USER_EMAIL` /
  `LANGFUSE_INIT_USER_PASSWORD` in `infra/env/langfuse.env`)

## Backups

The Supabase Postgres is the single backup target (one Postgres, ADR 0001).
`bootstrap.sh` enables a nightly `pg_dump` to the bucket from `infra/terraform`,
whose lifecycle rules are the retention policy (7 days / 4 weeks / 12 months).
The job uploads with the runtime key, which cannot delete objects. Restore
steps and the rehearsal log: [`backup/README.md`](backup/README.md).

Caddy's volumes (`caddy_data`, `caddy_config`) hold the TLS certificates; they
are re-issued on a rebuild.

Langfuse traces live in their own database and are **not** part of the dump.

## Trace retention

Langfuse traces hold the full conversation text. `bootstrap.sh` enables a daily
job ([`langfuse/retention.sh`](langfuse/retention.sh)) that deletes traces,
observations and scores stored more than 30 days ago; Langfuse v2 has no
setting for this (ADR 0007).

```bash
systemctl list-timers langfuse-retention.timer      # next run
journalctl -u langfuse-retention.service -n 10      # rows deleted per table
echo LANGFUSE_RETENTION_DAYS=60 > /srv/redreamai/infra/env/retention.env   # another period
```

## Updating Supabase

`cd /srv/supabase`, back up the database, then `sh update.sh` followed by
`sh run.sh pull && sh run.sh recreate` (Supabase's own procedure). Bump
`SUPABASE_REF` in `bootstrap.sh` to the release you moved to.

## Tearing down

```bash
cd /srv/redreamai/infra && docker compose down   # this stack only
cd /srv/supabase && sh run.sh stop               # Supabase; data stays in volumes/
```

The instance itself is protected against deletion; see
[`terraform/README.md`](terraform/README.md).

## Updating the app and backend

Publish matching mobile and backend releases when the consent text changes.
The app sends its bundled `text_version`; the backend requires it to match the
code constant `redream_api.core.consent.CONSENT_VERSION`. Bump it together with
Flutter's `consentTextVersion` and deploy the backend first (RELEASING.md).

Apply database migrations before starting updated containers. The deployment
workflow handles this ordering and runs
`docker compose exec -T api /app/.venv/bin/python -m redream_api.revoke_legacy_audio`
after startup to rotate legacy audio paths and refresh stored URLs. Run that
command from `infra/` when deploying manually.

Set the API's `SUPABASE_PUBLIC_URL` to the same public storage host as the AI
service. Both services default `REHEARSAL_URL_TTL_SECONDS` to 300 seconds;
existing audio URLs refresh through the owner-authenticated API endpoint.
Keep the Langfuse retention timer active with its 30-day default, consistent
with the app's privacy disclosure.
