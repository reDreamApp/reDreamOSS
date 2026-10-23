# Roadmap — known limits and possible next steps

What reDreamOSS does not do yet, roughly in order of how much it matters to an operator. Architecture decisions live in [`decisions/`](decisions/README.md); a change to one of them needs an ADR first.

## Limits an operator should know

- **Prompts tuned on Mistral.** The guide's stage rules and the crisis check were tuned on Mistral Medium 3.5 / Small 3.2. Other model families may route stages or classify risk less reliably; run `apps/server/evals` before switching. More eval cases and results for other families are welcome.
- **One process.** Rate limits and the daily cap live in memory: they reset on restart and do not work across several server containers. Running more than one worker needs a shared store first (an ADR).
- **No mail.** Password resets and account creation outside sign-up go through the operator CLI.
- **No moderation channel.** There is no in-app report button; people reach the operator through `OPERATOR_CONTACT`.
- **No request-flood protection at the edge.** The server limits sign-in, sign-up and AI requests; Caddy as configured adds nothing on top. Behind a tunnel or CDN, use its rate limiting.
- **Long dreams and OpenAI speech.** OpenAI's TTS takes at most 4096 characters per request; a longer rewritten dream fails to render there. Splitting at sentence boundaries and joining the MP3s would lift it.
- **iOS.** The app builds for iOS, but there is no release pipeline (TestFlight) and no automated iOS check; App Transport Security can block streaming rehearsal audio from a plain-http development server.

## Possible next steps

- iOS release workflow (TestFlight), once someone maintains an Apple developer account for the project.
- More languages than English and German (app texts, prompts, the language check, helpline defaults).
- Health and uptime notes for operators (an external ping on `/api/v1/health`).
- A shared limits store (Postgres) so the server can run more than one worker.
