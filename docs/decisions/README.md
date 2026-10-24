# Architecture Decision Records

The binding architectural decisions of reDreamOSS. Each ADR uses a lightweight MADR-style format: context, decision, consequences, alternatives. Numbers have gaps: the decisions that belonged to reDreamAI's hosted deployment (Supabase, Langfuse, the token budget, moderation emails, pinned provider models, Terraform on Scaleway) were dropped when reDreamOSS started; ADR 0013 lists them, and the history of the reDreamAI showcase repository keeps their full text.

## Index

- [0002 — Stateless AI side](0002-stateless-ai-service.md)
- [0003 — Structured outputs for the summary](0003-structured-outputs.md)
- [0004 — Position as self-help, not therapy](0004-self-help-framing.md)
- [0005 — Crisis detection before every guided turn](0005-crisis-detection.md)
- [0009 — Streaming chat via SSE end-to-end](0009-streaming-chat.md)
- [0013 — reDreamOSS: one self-hostable server, any provider](0013-origin-of-redreamoss.md)
- [0014 — Personal mode: the same server for one person, on their computer](0014-personal-mode.md)

## Status conventions

- **accepted** — current binding decision
- **superseded by ADR-NNNN** — historical, do not follow
- **proposed** — under discussion, not yet binding

## Workflow

1. New decision: create `NNNN-short-slug.md` with the next free number (0015 onwards), status `proposed`, open a pull request.
2. Revising an accepted decision: add a dated amendment, or write a new ADR that supersedes it and change the old one's status to `superseded by ADR-NNNN`. Keep superseded ADRs: they are the record of why the architecture is what it is.
