# 0013 — reDreamOSS: one self-hostable server, any provider

- Status: accepted
- Date: 2026-10-23
- Deciders: Daniel

## Context

reDreamAI ran as one hosted deployment: a Scaleway box provisioned with Terraform, self-hosted Supabase for login, storage and the database, a separate AI service, self-hosted Langfuse for traces, a per-user token budget, moderation reports by email, and pinned Mistral and Scaleway models. That shape suited one operator. It does not suit people who want to run the app for a small group themselves: too many services, accounts and provider-specific pieces, and an app built for one server.

reDreamOSS starts from the history of reDreamAI's public showcase repository (`reDreamApp/reDreamAI`) and keeps the product: guided rewriting, the crisis check, consent, the journal, rehearsal by reading or listening.

## Decision

1. **One server.** `apps/server` holds both packages, `redream_api` (users, chats, dreams, consent; source of truth) and `redream_ai` (guided chat, crisis check, speech), in one process with one worker. The API calls the AI side in-process through `BaseAiClient`; no AI function is reachable over HTTP. ADR 0002's stateless AI still holds inside the process.
2. **Plain Postgres and Docker Compose.** `compose.yaml` runs Postgres 17 and the server, optionally Caddy for HTTPS. The schema is numbered SQL files applied by the server at start; no Supabase, no Alembic.
3. **Own login.** Email and password (argon2id), opaque 30-day sliding tokens stored as hashes, invite-only sign-up by default, an operator CLI for accounts and password resets. No mail.
4. **Any OpenAI-compatible provider.** `LLM_*` for every text role, `STT_*` and `TTS_*` for speech, each optional and off without a model; the app hides voice features the server does not offer. Reasoning models and parameter differences are handled when requests are sent. Model IDs are the operator's choice; the examples in `.env.example` are pinned versions.
5. **Rehearsal audio on a volume**, behind short-lived HMAC-signed links.
6. **Cost guard without accounting.** A per-user daily request cap (`DAILY_AI_REQUESTS_PER_USER`, in memory) replaces the token budget; invite-only sign-up and the provider's own spend limit do the rest.
7. **One app for every server.** The app asks for the server address, checks `GET /api/v1/instance` (API and consent versions, features, operator contact and privacy policy) and follows what the server offers. Release builds talk https only. Android APKs are built from version tags and published as GitHub Releases.
8. **Operator-neutral texts.** Consent, privacy and onboarding name the user's server and its operator, never one company or provider; a test checks every user-facing text against ADR 0004.

Removed with the hosted setup, and recorded in the showcase repository's ADRs: Supabase and its pooler (ADR 0001, 0011), moderation emails and reports (0006), Langfuse (0007), the token budget (0008), the pinned-model policy for one deployment (0010), Terraform on Scaleway (0012). Also gone: help-center messages, the "delete my data only" action (deleting the account deletes everything) and the `safety_events` table (a flagged message keeps its `safety_flagged` mark; the log line carries no content).

## Consequences

### Positive
- An operator needs a box with Docker, one API key and about fifteen minutes.
- No data leaves the server except to the model providers the operator chose.
- Fewer moving parts to secure and update: two containers, one image.

### Negative
- One worker: limits are per process and reset on restart; the server does not scale horizontally.
- Prompts were tuned on Mistral models; other families may follow the stage rules less well (operators run the evals in `apps/server/evals`).
- No moderation channel and no mail: the operator handles reports and password resets by hand.
- Each operator is the data controller for their users' health data; the project only provides the software and a checklist (`docs/SELF_HOSTING.md`).

## Alternatives considered
- **Keep Supabase for login and storage**: a second stack of services for every operator, for features the server now does in a few hundred lines.
- **A hosted service for everyone**: one operator again, and the costs and duties of hosting other people's health data.
- **One build per server, address baked in**: every operator would need Flutter and a signing key; a preset address remains possible for those who want it (`--dart-define=DEFAULT_SERVER_URL`).
