# 0009 — Streaming chat via SSE end-to-end

- Status: accepted (AI-side endpoint **implemented 2026-07** — see Amendment below)
- Date: 2026-05-11
- Deciders: Daniel

## Context
Legacy AI service exposes `POST /chat/stream` (SSE) but neither the backend nor the frontend uses it — `chats/routes.py` calls non-streaming `POST /chat` and awaits the full response. For therapy chat (warm 200-word responses, 3–6s latency on Mistral Small 4) the blank-screen wait is a noticeable UX cost.

## Decision
Wire streaming end-to-end in v1:

1. **AI service** `POST /chat` returns `text/event-stream`. Each event is a chunk from Mistral's `chat.stream_async`. For non-summary stages, chunks are plain text. For the summary stage, chunks are accumulated and JSON is parsed at end-of-stream before emitting the structured fields as a final SSE event (see ADR 0003).
2. **Backend** `POST /api/v1/chats/{id}/message` forwards the SSE stream to Flutter. Persists the final assembled message to Postgres at end-of-stream. Token-usage counter (ADR 0008) is incremented at end-of-stream.
3. **Caddy** reverse proxy is SSE-friendly by default (no `proxy_buffering` config needed, unlike nginx).
4. **Flutter** consumes via `http.Client().send()` + UTF-8 SSE parsing. Custom `MessageBubble` for assistant messages renders a `StreamBuilder<String>` of accumulating text.

## Consequences

### Positive
- Warmth arrives word-by-word; chat feels alive instead of "system thinking".
- Backpressure handling falls out naturally (Flutter pauses → backend pauses → Mistral SDK pauses).

### Negative
- More code on every layer (vs. an `await` + render).
- Summary stage cannot stream tokens (waits for valid JSON). Mitigation: send a "thinking…" frame so the UI isn't fully blank.
- Reconnection / mid-stream failure handling needs to be designed (likely: on disconnect, persist what we have and let the next user message resume — accept that the stream is one-shot).

## Alternatives considered
- **Non-streaming (legacy)**: rejected — UX cost is real for therapy chat.
- **WebSocket instead of SSE**: bidirectional unnecessary; SSE is simpler and proxy-friendly.
- **Polling**: rejected — wasteful.

## Amendment (2026-07-02) — AI-side SSE endpoint implemented

The AI service now exposes `POST /chat/stream` (SSE) alongside the non-streaming
`POST /chat`. The frame contract is authoritative in
`apps/ai/src/redream_ai/api/chat.py`'s module docstring: verbatim content chunks →
optional safety frame → one final `ChatResponse` frame → `data: [DONE]`; the core's
internal usage frame is folded into `final.usage` and never forwarded. The backend
proxy `POST /chats/{id}/message/stream` (apps/api) forwards content deltas to the
Flutter client and persists the turn from the final frame; the Flutter client consumes
the proxy's frames (`ChatState.send` streaming path). All three layers are implemented
— frame-shape changes are breaking cross-service changes.

## Amendment (2026-10-23) — reDreamOSS: one process

The stream no longer crosses a service boundary: `POST /api/v1/chats/{id}/message/stream` iterates the in-process AI stream (`InProcessAiClient`, closed with `aclosing` at every layer when the client goes away) and forwards content deltas to the app. The frame contract is the one in `apps/server/src/redream_ai/api/chat.py`. Caddy (optional) and tunnels pass SSE through unbuffered; operators with their own proxy must turn response buffering off (`docs/SELF_HOSTING.md`).
