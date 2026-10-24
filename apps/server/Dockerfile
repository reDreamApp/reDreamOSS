FROM python:3.12-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# At least the uv that wrote uv.lock (lock revision 3), so --frozen can read it.
COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-install-project --no-dev

COPY src ./src
# The schema, applied by redream_api.db.migrate (found at /app/migrations).
COPY migrations ./migrations
RUN uv sync --frozen --no-dev

RUN useradd --no-create-home --uid 10001 appuser
# Rehearsal MP3s (AUDIO_DIR's default, data/audio): the app user's alone;
# mount a persistent volume here to keep them across container rebuilds.
RUN mkdir -p /app/data/audio \
    && chown -R appuser /app/data \
    && chmod 700 /app/data /app/data/audio
USER appuser

# Run from the synced venv directly: `uv run` at runtime needs a writable
# $HOME cache, which the no-home appuser doesn't have.
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/v1/health || exit 1

# Pending migrations first (waits up to 30 s for the database; a failure
# stops the container before the server starts), then the server. Exactly one
# worker, stated explicitly: per-process state (the in-memory rate limits and
# daily AI-request counters) must not be split across processes.
# Proxy headers: the client address (the rate limits' key) comes from
# X-Forwarded-For when the peer is in FORWARDED_ALLOW_IPS. The default,
# 127.0.0.1, trusts no other peer: safe for a plain docker run. compose.yaml
# sets its own network's subnet (Caddy and the Docker proxy behind its
# 127.0.0.1-only port). Addresses or CIDRs, comma-separated; never "*", with
# which uvicorn takes the leftmost entry, the one a client writes itself.
# Quoted, so the shell neither splits nor globs it.
# On a stop, open chat streams get 20 s to finish; then they are cancelled
# and roll back their unanswered message, within compose's stop_grace_period
# (30 s). Without the limit uvicorn waits on them until Docker kills it.
CMD ["sh", "-c", "python -m redream_api.db.migrate && exec uvicorn redream_api.main:app --host 0.0.0.0 --port 8000 --workers 1 --no-access-log --proxy-headers --forwarded-allow-ips \"${FORWARDED_ALLOW_IPS:-127.0.0.1}\" --timeout-graceful-shutdown 20"]
