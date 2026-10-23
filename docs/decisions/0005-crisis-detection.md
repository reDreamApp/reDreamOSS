# 0005 — Crisis detection before IRT routing

- Status: accepted
- Date: 2026-05-11
- Deciders: Daniel

## Context
The app is mental-health adjacent. Legacy IRT prompts have anti-violence guardrails on the LLM's output, but nothing scans user input for suicidal ideation, abuse disclosure, child-safety concerns, or imminent danger. A user in crisis routed into a normal IRT rewriting exercise is a poor outcome and a liability concern even under the self-help framing of ADR 0004.

## Decision
Add a lightweight pre-classifier on the AI service that runs before every IRT turn:

**Endpoint**: `POST /safety/classify`

```
request:  { "text": str, "language": "en"|"de" }
response: {
  "risk": "none" | "low" | "high",
  "categories": ["suicidal_ideation" | "self_harm" | "abuse_disclosure"
                 | "child_safety" | "imminent_danger" | "violence_to_others"],
  "confidence": float
}
```

**Model**: Ministral 8B with JSON mode + a strict schema prompt. Calibrated to err on false positives (low harm: shows helpline; high cost: missed crisis). *Amended 2026-10-21:* Mistral Small 3.2 on Scaleway, Ministral 8B on direct Mistral as fallback (ADR 0010).

**Backend flow** in the chat message handler:
1. Call `/safety/classify`.
2. If `risk == "high"`: persist user message with `safety_flagged=True`, return a `safety_event` payload (helpline list, "are you safe?" prompt), do NOT call `/chat`, do NOT advance the IRT stage.
3. Otherwise proceed to `/chat` as normal.

**Frontend**: render a non-dismissable card with country-localised helplines (single source in `data/helplines.json`, consumed by backend and frontend) + a tap-to-call link + a "Yes, I'm safe" dismiss.

**Audit**: new `safety_events` table — `(id, user_id, chat_id, message_id, category, confidence, action_taken, created_at)`. Retention 180d, same as reports.

## Consequences

### Positive
- Crisis signals are caught before the LLM gets a chance to produce a poorly-calibrated response.
- Country-localised helplines (DE: Telefonseelsorge 0800 111 0 111 + 116 117; EN: Samaritans / 988; emergency 112 / 911) are surfaced reliably.
- Auditable: every high-risk event is logged.

### Negative
- +1 LLM call per user turn (Ministral 8B ≈ 200ms). Acceptable.
- False positives are visible (user sees helpline overlay even when not in crisis). Mitigation: tap-to-dismiss; review classifier prompt quarterly against a fixture set.

## Alternatives considered
- **No detection (legacy)**: rejected — ethical and legal exposure.
- **Hosted hate-speech / safety API**: cheaper but ties us to a third-party processor outside the EU. Mistral is EU-resident.
- **Heuristic keyword list**: dramatically worse recall + accent/idiom sensitivity.

## Amendment (2026-10-23) — reDreamOSS

- The check is an in-process function (`InProcessAiClient.safety_classify`; the provider call times out after `LLM_SAFETY_TIMEOUT_SECONDS`, default 20 s), no longer `POST /safety/classify`. Its model is `LLM_SAFETY_MODEL` (default `LLM_SMALL_MODEL`) at the operator's provider, with no fallback: a failed check fails the chat turn and removes the just-saved message, it never lets the message through unchecked.
- The `safety_events` table is gone. A flagged message keeps `safety_flagged`; the server logs one line without chat id or content.
- The helpline text is configuration (`HELPLINE_TEXT_EN` / `_DE`, `HELPLINE_PROMPT_EN` / `_DE`) with a neutral default (local emergency number and findahelpline.com) instead of fixed German and English numbers; operators set their users' country's services.
- `SAFETY_CLASSIFIER_ENABLED` stays (default on, a warning when off): the classifier is not perfect, and operators testing models need to switch it off deliberately rather than by editing code.
