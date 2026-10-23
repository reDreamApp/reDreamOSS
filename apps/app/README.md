# apps/app — reDreamAI Flutter client

Flutter mobile app (iOS + Android). Talks to `apps/server`, which also signs users in.

## Setup

```bash
fvm use                       # Flutter version pinned in .fvmrc
fvm flutter pub get           # also generates lib/l10n/generated/
```

## Run / check

```bash
fvm flutter run
fvm flutter analyze
fvm flutter test
fvm dart format .
```

The app asks for its server's address on first start and checks it with
`GET /api/v1/instance` (same API and consent version as the app, or it says
which side needs an update). One build works with every server; release
builds accept `https://` only. A local server is `http://localhost:8001`
(iOS simulator / desktop) or `http://10.0.2.2:8001` (Android emulator); type
the `http://`, since an address without a scheme is taken as `https://`. An
operator's own build can preset the address, which is then tried without
asking: `fvm flutter build apk --dart-define=DEFAULT_SERVER_URL=https://dreams.example.org`.
"Change server" (sign-in screen, settings) signs out first. Sign-in is the server's own
email/password login: it returns an opaque bearer token, kept in the secure
storage together with the server it belongs to. The server slides the token
on use, so the app never expires it itself; a 401 signs out. Test users come
from the server's operator CLI (`python -m redream_api.cli create-user EMAIL`,
see `apps/server/README.md`); it also resets passwords, since the server sends
no mail.

## Layout

```
lib/
  main.dart            bootstrap + dependency wiring
  app.dart             MaterialApp, theme/locale, ServerGate → AuthGate → UserScope → consent gate → onboarding or shell
  core/                app-wide plumbing, no feature knowledge
    api/               ApiClient (JSON, SSE stream, multipart; address read per request, bearer token, 401 hook, error mapping, resolve() for server links)
    auth/              AuthService (ChangeNotifier), TokenAuthService (/api/v1/auth), SessionStore
    server/            ServerConfig (address, probe of /api/v1/instance, cached InstanceInfo)
    settings/          AppSettings (theme mode, language, onboarding flag)
    storage/           LocalStore (shared_preferences as JSON key/value)
    theme/             brand palette, BrandTheme extension, light/dark ThemeData
    widgets/           AppCard, AppButton, BrandLogo, ThemeModeToggle, IntensitySlider, snackbars/dialogs
    utils/             error → message mapping, date formatting
  features/            one folder per feature: models, service (HTTP), state (ChangeNotifier), screens
    shell/             MainShell (app bar, 4 tabs, chat drawer), UserScope (per-user providers + nested navigator)
    home/              home tab, progress stats + mood, IRT guide
    chat/              Session tab: streamed replies (SSE), voice input, rating card after a session, history drawer, reports
    consent/           Art. 9 consent gate + screen; withdrawal lives in settings
    journal/           dreams: offline-capable JournalState, cards, detail, editor, practice (reading/audio)
    reminders/         daily local notification + settings tile
    settings/          profile, account (email/password/export/delete), help center, privacy policy
    auth/              sign in / sign up (invite code) / forgot password note
    server/            ServerGate + address screen, changeServer()
    onboarding/        post-sign-up intro + profile questions
    learn/             the Learning tab: built-in articles (the team's study), each with a page in the app and a link to the browser
  l10n/                app_en.arb / app_de.arb (every user-facing string)
```

Rules of thumb:

- Screens only read/watch a `*State` and call its methods; states own the
  data and call a `*Service`; services only talk HTTP through `ApiClient`.
- Per-user state lives under `UserScope`, keyed by user id, so signing out
  disposes it and signing in starts clean. `UserScope` owns a nested
  `Navigator`, so pushed screens (settings, dream details, practice) can read
  the per-user providers. Dialogs use the root navigator: pass state in.
- Chat stages are `recording → rewriting → summary → final`. The chat never
  asks for the 0–10 rating; the finished panel does (`PUT /chats/{id}/intensity`).
- Voice input records WAV (16 kHz mono) and puts the transcript into the input
  field for review; it is never sent automatically.
- Offline: the journal caches the last server list and queues drafts and
  practices in `LocalStore`; `JournalState.syncPending()` flushes them on the
  next refresh or app resume. Chat keeps the last failed message for resend.
- `flutter test` covers `ApiClient`, `JournalState`, `ChatState` (streaming,
  session switching, voice, rating), `ConsentState` and the main screens
  against an in-memory fake API (`test/fake_api.dart`).
- `integration_test/live_smoke_test.dart` drives the real app against a real
  stack (sign-in, consent, one streamed AI turn), with the server given as
  `--dart-define=DEFAULT_SERVER_URL=...`. It is not part of CI.
