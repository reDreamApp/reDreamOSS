# 0002 — Stateless AI service

- Status: accepted
- Date: 2026-05-11
- Deciders: Daniel

## Context
Legacy `ai/api.py:29` keeps `conversations: Dict[str, Conversation]` in process memory, keyed by `session_id`. Backend periodically calls `POST /chat/set_history` to re-sync after restarts. The backend's `chat_messages` table is already the source of truth for conversation history, so this duplicate storage is fragile — lost on AI restart, can't horizontally scale, and produces divergence bugs between backend and AI.

## Decision
The AI service is stateless. Every `POST /chat` request carries the full conversation history. The AI service returns the next stage + response; it retains nothing between requests. Remove these endpoints from the legacy AI surface during the rebuild:

- `POST /chat/set_history`
- `GET /conversation/{session_id}`
- `DELETE /conversation/{session_id}`

`session_id` may still be passed for Langfuse trace grouping but is not used as a key into in-process state.

## Consequences

### Positive
- Backend is the only source of truth for chat history. No re-sync logic.
- AI service scales horizontally without sticky sessions.
- No data lost on AI restart or redeploy.
- A whole class of "backend and AI disagree" bugs is gone.

### Negative
- Every `POST /chat` request payload includes the full history → slightly larger requests. At realistic conversation lengths (≤50 messages) this is a few KB. Negligible.
- Future "AI remembers across sessions" features (e.g. cross-conversation context) need explicit storage in Postgres, not implicit in-memory.

## Alternatives considered
- **Keep in-memory cache, refresh from backend on miss**: still requires sync, still loses state on restart, just adds a cache layer.
- **Move state to Redis**: introduces a new service for marginal gain. Postgres already stores it.

## Amendment (2026-10-23) — reDreamOSS: in-process

The AI side is no longer a separate service: it runs in the server's process and the API calls it through `InProcessAiClient` (ADR 0013), so there is no `POST /chat` over HTTP at all. The decision stands as written for the function boundary: every chat call carries the full history from Postgres, and the AI side keeps nothing between calls. `session_id` is no longer used for tracing (Langfuse is gone).
