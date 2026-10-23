# 0003 — Structured outputs for routing and summary

- Status: accepted (routing output shape **amended 2026-07-02** — see Amendment below)
- Date: 2026-05-11
- Deciders: Daniel

## Context
Legacy backend has `_parse_summary` in `backend/.../chats/routes.py:67-124`: a string-matching parser scanning LLM output for headings like `"dream summary"`, `"traumzusammenfassung"`, `"Are you satisfied…"`. This works because the legacy prompts happen to produce those exact headings. The moment the LLM phrases summaries differently (Groq → Mistral Small 4), dream auto-creation silently breaks. Similarly the routing agent's "single word stage name" is parsed by free-text matching — fragile.

## Decision
Use Mistral's structured outputs (JSON mode + schema) on the AI service for two endpoints:

1. **Routing agent** — returns `{"stage": "recording"|"intensity"|"rewriting"|"summary"|"final", "confidence": float}`.
2. **Summary stage of `POST /chat`** — when `stage == "summary"`, response includes `structured: {title, original_summary, rewritten_summary, dream_summary}`.

Backend reads these fields directly and stores them into `dreams` columns. No string matching.

## Consequences

### Positive
- Model swaps don't silently break parsing.
- Summary fields are typed at the contract layer, not inferred.
- Routing JSON makes A/B-ing routing models trivial (Ministral 8B vs 14B).

### Negative
- Streaming chat (ADR 0009) for the summary stage is harder — must accumulate JSON until valid before emitting. For non-summary stages, plain text streaming is fine.
- Schema changes become a versioned contract (see `docs/contracts.md` once written).

## Alternatives considered
- **Keep `_parse_summary`, port verbatim**: rejected; flagged as one of the legacy pitfalls.
- **Function calling / tool use** instead of `response_format`: equivalent in shape, but JSON mode is simpler for non-tool semantics.

## Amendment (2026-07-02) — routing no longer uses JSON; summary requirement unchanged

The 2026-07 core port replaced the routing agent: it emits a one-word stage name, parsed
via the `Stage` enum with a stay-in-current-stage fallback (turn 1 defaults to `recording`)
— see `apps/ai/src/redream_ai/core/irt_app.py::determine_stage_async`. The routing-JSON
shape above (`{"stage": ..., "confidence": ...}`) is superseded; no confidence value is
returned.

The structured-output requirement **remains in force for the summary stage**: the ported
core's summary prose gets a JSON-mode extraction pass
(`apps/ai/src/redream_ai/summary_structured.py`) that fills `ChatResponse.structured`.
The `_parse_summary` ban is untouched — no string-matching of LLM prose, anywhere.

## Amendment (2026-10-23) — reDreamOSS: any provider

The code moved to `apps/server/src/redream_ai/` (`core/irt_app.py`, `summary_structured.py`). JSON mode is requested from whatever OpenAI-compatible provider the operator configures (`LLM_JSON_MODE`, default on; off for endpoints without `response_format`). The summary extraction and the crisis check both read the answer with `redream_ai/llm/json_parse.parse_json_object`, which skips code fences, prose and `<think>` blocks and accepts exactly one JSON object, otherwise raises. A failed summary extraction leaves `structured` empty: the prose is still shown, but no journal entry is created from that summary. A failed crisis check fails the turn. The ban on string-matching model prose is unchanged.
