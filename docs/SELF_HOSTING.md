# Running a reDreamOSS server

One server serves everyone you invite. The app is the same for every server: people install it, type your server's address once and sign up with your invite code. Nothing goes through the project; you are the operator.

Only for yourself? You need no server: `make start` runs reDreamOSS on your own computer, in the browser (see the [README](../README.md#for-yourself-on-your-computer)).

## What you need

- A Linux box with Docker Engine and the Compose plugin. 1 vCPU, 1–2 GB RAM and a few GB of disk are plenty for a small group; the models run at your provider, not on the box.
- An API key for an OpenAI-compatible chat-completions endpoint (Mistral, Scaleway Generative APIs, OpenAI, a self-hosted vLLM or Ollama, …). Speech is optional and can come from the same or another provider.
- An https address for the server: your own domain (Caddy gets the certificate) or a tunnel (no open ports). Release builds of the app refuse plain http.

## Install

```bash
git clone https://github.com/reDreamApp/reDreamOSS && cd reDreamOSS
sh scripts/init-env.sh            # writes .env (mode 600) with a database password and an invite code, prints the code
```

Open `.env` and fill in the **Required** block at its top: `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` (replies and the dream summary) and `LLM_SMALL_MODEL` (routing, crisis check, small checks). [`.env.example`](../.env.example) documents every setting and has ready examples for Mistral and Scaleway. Then:

```bash
docker compose up -d --build --wait
curl -s http://127.0.0.1:8000/api/v1/health      # {"status":"ok","llm_ready":true,...}
```

The server starts after Postgres is healthy, applies the database migrations itself and listens on `127.0.0.1:8000` only.

### Models

The guide's prompts were written and tuned on Mistral models (Mistral Medium 3.5 and Small 3.2 on Scaleway); other model families may follow the stage rules less well. Before inviting people with another model, run the two live checks against your `.env` — they call your provider and cost a few cents. They need [uv](https://docs.astral.sh/uv/) and this checkout (the evals are not in the server image), on the box or on any machine with the same `LLM_*` values:

```bash
cd apps/server
uv run --env-file ../../.env python evals/safety_regression.py      # the crisis check on fixed cases
uv run --env-file ../../.env python evals/rewriting_regression.py   # the rewriting stage's opener and the summary→final routing
```

Reasoning models (OpenAI o-series, gpt-5, …) work: they are recognised by name or listed in `LLM_REASONING_MODELS`. A model that refuses a parameter is retried once without it and remembered.

### Speech (optional)

Voice input needs `STT_MODEL` (an OpenAI-style `/audio/transcriptions` endpoint), rehearsal audio needs `TTS_MODEL` and a voice. Without them the app simply hides the microphone and the Listen button. A different host than `LLM_BASE_URL` needs its own key (`STT_API_KEY`, `TTS_API_KEY`): the LLM key is never sent to another host.

### The crisis check and helpline text

Every message is checked for acute risk before the guide answers; a flagged message gets a helpline text instead of a reply. The default text points to the local emergency number and findahelpline.com. Set `HELPLINE_TEXT_EN` / `HELPLINE_TEXT_DE` (and the short `HELPLINE_PROMPT_*` the guide names on real distress) to the services of the country your users live in. Keep `SAFETY_CLASSIFIER_ENABLED=true`: the check is not perfect, and off it is not there at all.

## HTTPS

Pick one.

**Caddy on the box** (ports 80 and 443 open, a DNS name pointing at the box): set `DOMAIN=dreams.example.org` and `COMPOSE_PROFILES=caddy` in `.env`, run `docker compose up -d --wait` again. Caddy fetches and renews the certificate. Point DNS straight at the box (with Cloudflare: "DNS only"); behind a CDN proxy every client would look like the CDN to the server's rate limits.

**A tunnel, no open ports** — a named Cloudflare Tunnel to `http://127.0.0.1:8000`, or Tailscale Funnel (`tailscale funnel 8000`). The tunnel gives you an https address; keep the `caddy` profile off. Use an address that stays the same: Cloudflare's quick tunnel (`cloudflared tunnel --url …`) gets a new random trycloudflare.com address on every restart, and the app keeps the one people typed. Tunnels add an `X-Forwarded-For` header, which the server's per-address limits read. These two paths are not part of the project's automated checks: if people on different networks get "too many attempts" at the same time, the tunnel is not passing `X-Forwarded-For` and every client looks like one.

**Your own reverse proxy**: proxy to `127.0.0.1:8000`, do not buffer responses (the chat streams), and set or append `X-Forwarded-For`.

Then check from outside: `https://your-address/api/v1/instance` shows your server's name and features.

## Accounts

`REGISTRATION_MODE=invite` (the default) lets people sign up with `INVITE_CODE`; `open` lets anyone sign up, `closed` nobody. The server sends no mail, so you create accounts and reset passwords from the box:

```bash
docker compose exec server python -m redream_api.cli create-user someone@example.org
docker compose exec server python -m redream_api.cli set-password someone@example.org
```

`DAILY_AI_REQUESTS_PER_USER` (default 100, 0 = off) caps each person's chat turns, transcriptions and rehearsal renderings per UTC day. It is the only cost guard on the server: set a monthly spend limit at your provider as well.

## The app

Point people to the APK on the [releases page](https://github.com/reDreamApp/reDreamOSS/releases/latest). On first start it asks for the server address, checks `/api/v1/instance` and says when the app or the server needs an update. Fill in the **Instance** block in `.env` so people see whom they are dealing with: `INSTANCE_NAME`, `OPERATOR_NAME`, `OPERATOR_CONTACT` (an email address or a URL; shown under Settings → Contact and on "Forgot password?") and `PRIVACY_URL` (your privacy policy, linked in Settings and on the consent screen).

Your own build, with your address preset so nobody types it: `cd apps/app && fvm flutter build apk --release --dart-define=DEFAULT_SERVER_URL=https://dreams.example.org`, signed with your own key and with your own `applicationId` in `android/app/build.gradle.kts`, so it installs next to the project's APK instead of over it. iOS builds need a Mac with Xcode and your own Apple developer account.

## Updates

```bash
git pull
make update          # pulls newer Postgres/Caddy images, rebuilds the server on a fresh base image, restarts
```

The server migrates the database as it starts. Update the server before people update the app: a new app may need the new server (it tells them so), never the other way round.

## Backups

Two volumes hold everything: `redream_pgdata` (accounts, chats, dreams) and `redream_audio` (the rehearsal MP3s). `.env` holds the database password and your keys; keep a copy somewhere safe, not next to the backups.

```bash
# back up (while running)
docker compose exec -T db pg_dump -U redream -d redream > redream.sql
docker run --rm -v redream_audio:/audio:ro -v "$PWD":/backup alpine tar czf /backup/audio.tgz -C /audio .

# restore onto a fresh box (same .env), before the server has ever started there;
# if it has, `docker compose down -v` first (this deletes that box's data)
docker compose up -d --wait db
docker compose exec -T db psql -U redream -d redream -v ON_ERROR_STOP=1 < redream.sql
docker compose create server       # creates the audio volume the way compose expects
docker run --rm -v redream_audio:/audio -v "$PWD":/backup alpine \
  sh -c 'tar xzf /backup/audio.tgz -C /audio && chown -R 10001:10001 /audio && chmod 700 /audio'
docker compose up -d --build --wait
```

The dumps contain health-related data in plain text: encrypt them (for example with `age` or `gpg`) before they leave the box, and delete old ones. A restore you have never tried is not a backup: try one on a scratch box.

## Limits

- One server process (`--workers 1`): the rate limits and the daily cap are counted in memory and reset on restart. Do not scale the server container.
- The server logs no message content and no client addresses in access logs; `docker compose logs` keeps a few rotated MB per service.
- The apps are Android, iOS and the web app; no desktop builds. A shared server can serve the web app too: put the release's `reDreamOSS-web-*.zip` unpacked into a folder and set `WEB_DIR` to it (in compose, mount it into the server container).

## Your duties as the operator

This is not legal advice, but a checklist of what running a server for others means, especially in the EU:

- **You are the controller** of the data people enter: dream descriptions, moods and ratings are health-related data (GDPR Art. 9). The app asks for explicit consent and names your server address; the rest is yours.
- **Privacy policy**: who you are, what is stored where and for how long, which providers receive what (your model and speech providers), people's rights and how to reach you. Publish it and set `PRIVACY_URL`.
- **Processors**: sign the data-processing agreements your providers offer (Art. 28), choose regions and retention settings deliberately (some providers keep inputs for abuse monitoring unless you ask for zero retention), and say so in the policy.
- **Requests**: people can export their data and delete their account in the app; handle everything else (questions, corrections) through `OPERATOR_CONTACT`.
- **Security**: keep the box updated, keep `.env` and backups private, and report a breach within 72 hours where the GDPR applies.
- **Assessment**: for health data at any real scale, a data-protection impact assessment (Art. 35) is usually required.
- **Framing**: reDreamOSS is a self-help tool, not therapy and not a medical device. Do not advertise it with treatment or effect claims, and keep the disclaimers the app shows. The app is meant for adults (sign-up says 18+).
