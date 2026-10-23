"""The Art. 9 consent-text version users must have granted.

Read as `consent.CONSENT_VERSION` at call time (import the module, not the
name), so tests can monkeypatch it.
"""

from __future__ import annotations

# Must equal consentTextVersion in apps/app/lib/features/consent/consent_service.dart
# (tests/api/test_consent.py checks). Bump both together, and ONLY on material
# changes to the consent copy (new processor, new purpose): a bump makes every
# user consent again on next launch. Deploy the server before the app
# (RELEASING.md): an app with the new version is refused by an old server.
CONSENT_VERSION = "2026-10-07"
