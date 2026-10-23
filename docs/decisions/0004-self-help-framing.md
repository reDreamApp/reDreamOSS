# 0004 — Position as self-help, not therapy

- Status: accepted
- Date: 2026-05-11
- Deciders: Daniel

## Context
reDreamAI draws on imagery-rehearsal techniques and guides users through a dream-rewriting exercise in an LLM-driven conversation. It is meant as a self-help tool, not as a treatment, and its wording has to make that clear to users.

## Decision
Frame the app as a **self-help tool based on CBT / IRT principles**. Every user-facing surface that could be read as a clinical claim carries this one-liner:

> reDream is a self-help tool based on CBT / IRT principles. It is not a substitute for psychotherapy.

Required surfaces: onboarding screen 1, chat header (subtle), privacy policy, support email auto-reply, App Store / Play Store descriptions.

Prompt copy must:
- Avoid first-person clinician framing ("as your therapist…", "I diagnose…", "I treat…").
- Avoid diagnostic statements ("you have PTSD", "this is trauma").
- Keep symptom severity to lay terms (0–10 intensity, daily mood). No PCL-5 or similar instruments.

## Consequences

### Positive
- Clear user expectations: a tool, not a replacement for care.

### Negative
- No outcome claims ("reduce nightmares by X%").
- Helpline overlay copy (ADR 0005) must reinforce "we are not a service provider" framing.

## Alternatives considered
- **Clinical or therapeutic positioning**: not chosen; outside the scope of this project.

## Amendment (2026-10-23) — reDreamOSS

Applies unchanged to reDreamOSS and to every server built from it. `apps/app/test/wording_test.dart` checks all user-facing texts (EN, DE, the iOS microphone text): no therapy, treatment or effect claims and no named conditions outside the exact negated disclaimer phrases, and no operator or provider names. Operators are asked not to advertise their server with such claims either (`docs/SELF_HOSTING.md`).
