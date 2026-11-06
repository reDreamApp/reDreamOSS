# Releasing

`main` is the only long-lived branch; CI proves every push (server tests and lint, the Postgres schema from zero on Postgres 17 against the ORM models, the compose stack built and started with a login, the Flutter analyzer and tests). A release is a version tag on a commit of `main`: `vMAJOR.MINOR.PATCH`.

- **The app**: pushing the tag runs `.github/workflows/release-apk.yml`. It refuses a tag whose commit is not on `main`, analyzes and tests the app, builds the web app and attaches it (`reDreamOSS-web-<tag>.zip` + `.sha256`) to a GitHub Release: `make start` downloads the one for the checkout's newest tag, so a tag without a release leaves personal-mode users to build it themselves. Once the signing key below is set up, the same release carries a release APK signed with the project's upload key (version name from the tag, version code derived from it: 1.2.3 → 1002003; `pubspec.yaml`'s version is not used), with its SHA-256. One APK serves every server. Without the four secrets the release has the web app only; with some but not all of them the run fails.
- **The server**: operators update with `git pull` and `make update`; the server container applies pending migrations before it starts (a failed migration stops it), so code and schema always match.

## Order of changes

The app checks the server's `GET /api/v1/instance` when people connect: `api_version` and `consent_version` must match its own.

- **A breaking API change** (a removed or renamed field or route, a changed meaning): bump `API_VERSION` in `apps/server/src/redream_api/api/v1/instance/routes.py` and `appApiVersion` in `apps/app/lib/core/server/server_config.dart` together. Prefer additive changes; they need no bump.
- **A consent-text change**: the version is a code constant on each side, `redream_api.core.consent.CONSENT_VERSION` and `consentTextVersion` in `apps/app/lib/features/consent/consent_service.dart`; a server test keeps them equal. Bump both, and only on a material change to the consent copy (a new kind of data, purpose or recipient): everyone consents again.
- Either way: **servers first, then the app**. Say so in the release notes, so operators update before people install the new APK. An app that is newer than its server tells people the server needs an update; it cannot work around it.

## How a schema change ships

1. Add the next numbered file, `apps/server/migrations/000N_feature_x.sql` (plain SQL without BEGIN/COMMIT: the runner applies each file and its `schema_migrations` row in one transaction), and change the ORM models to match, in the same commit. Never edit a file that was applied anywhere: the runner refuses one whose checksum changed.
2. Add the same change for personal mode's SQLite files: an entry for the new number in `SQLITE_STEPS` (`apps/server/src/redream_api/db/migrate.py`), SQLite statements that bring an existing file from the previous version to this one. A test fails without it.
3. Check locally: `make db-migrate`, then `make db-test` with `TEST_DATABASE_URL` pointing at an empty, throwaway database.
4. Merge to `main`; CI's `postgres-schema` job re-proves it from zero.

The default server tests build their tables from the ORM models on SQLite, so on their own they never see a model change without its migration (or the reverse); `tests/pg` does.

## Cutting a release

```bash
git switch main && git pull
git tag -a v1.2.0 -m "reDreamOSS 1.2.0"
git push origin v1.2.0
```

Then edit the release's notes on GitHub: what changed for people, what changed for operators (new settings, the update order above).

## One-time: repository settings

- **Private vulnerability reporting** (Settings → Code security): on, so SECURITY.md's "Report a vulnerability" exists.
- **Tag protection**: a ruleset for tags `v*` that only maintainers may create. The workflow signs only tags whose commit is on `main`, but anyone who can push a tag can start it.
- **Optional, stronger**: move the four secrets below into an environment `release` with required reviewers and add `environment: release` to the job, so every signed build waits for an approval.

## One-time, optional: the signing key

For the APK the release workflow needs four repository secrets; without them releases carry the web app only. Create the upload key once, on your own machine:

```bash
keytool -genkeypair -v -keystore upload-keystore.jks -alias upload \
  -keyalg RSA -keysize 4096 -validity 10000
base64 -i upload-keystore.jks | gh secret set ANDROID_KEYSTORE_BASE64 --repo reDreamApp/reDreamOSS
gh secret set ANDROID_KEYSTORE_PASSWORD --repo reDreamApp/reDreamOSS
gh secret set ANDROID_KEY_ALIAS --repo reDreamApp/reDreamOSS --body upload
gh secret set ANDROID_KEY_PASSWORD --repo reDreamApp/reDreamOSS
```

(`gh secret set` without `--body` asks for the value. keytool's default PKCS12 keystore has one password: `ANDROID_KEY_PASSWORD` is the same value as `ANDROID_KEYSTORE_PASSWORD`.) Keep `upload-keystore.jks` and its passwords in two safe places, offline: an APK signed with another key cannot update an installed one, so a lost key means everyone uninstalls (and loses the app's local data) to get the next version. Delete the copy from the working directory; `*.jks` and `key.properties` are gitignored, but nothing else protects them.

Locally, `android/key.properties` (`storeFile` relative to `android/app`, `storePassword`, `keyAlias`, `keyPassword`) makes `fvm flutter build apk --release` sign with that key; without it a release build is signed with the debug key and is for your own devices only.
