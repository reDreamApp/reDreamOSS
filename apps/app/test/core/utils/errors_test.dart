import 'package:flutter_test/flutter_test.dart';
import 'package:redream_app/core/api/api_exception.dart';
import 'package:redream_app/core/auth/auth_service.dart';
import 'package:redream_app/core/utils/errors.dart';
import 'package:redream_app/l10n/generated/app_localizations.dart';
import 'package:redream_app/l10n/generated/app_localizations_de.dart';
import 'package:redream_app/l10n/generated/app_localizations_en.dart';

void main() {
  for (final l10n in <AppLocalizations>[
    AppLocalizationsEn(),
    AppLocalizationsDe(),
  ]) {
    group(l10n.localeName, () {
      test('every AuthFailure kind has its own message', () {
        final expected = {
          AuthFailureKind.invalidCredentials: l10n.signInError,
          AuthFailureKind.emailTaken: l10n.authEmailTaken,
          AuthFailureKind.registrationClosed: l10n.authRegistrationClosed,
          AuthFailureKind.inviteInvalid: l10n.authInviteInvalid,
          AuthFailureKind.wrongPassword: l10n.changePasswordWrongCurrent,
          AuthFailureKind.tooManyAttempts: l10n.authTooManyAttempts,
          AuthFailureKind.invalidInput: l10n.authInvalidInput,
          AuthFailureKind.sessionExpired: l10n.authSessionExpired,
          AuthFailureKind.personalKey: l10n.personalKeyNeeded,
          AuthFailureKind.other: l10n.errorUnknown,
        };
        expect(expected.keys, unorderedEquals(AuthFailureKind.values));
        for (final MapEntry(:key, :value) in expected.entries) {
          expect(describeError(AuthFailure(key), l10n), value, reason: '$key');
        }
      });

      test('the daily limit, a throttle and voice off read differently', () {
        expect(
          describeError(ApiException(429, 'daily limit reached'), l10n),
          l10n.dailyLimitBody,
        );
        expect(
          describeError(ApiException(429, 'slow down'), l10n),
          l10n.errorRateLimited,
        );
        expect(
          describeError(ApiException(503, 'voice_unavailable'), l10n),
          l10n.voiceUnavailable,
        );
      });

      test("a server failure never shows the server's own words", () {
        for (final code in [500, 502, 503, 504]) {
          expect(
            describeError(
              ApiException(code, 'safety: crisis classifier not configured'),
              l10n,
            ),
            l10n.errorServer,
            reason: '$code',
          );
        }
        // A client error's detail is the server telling people what to fix.
        expect(
          describeError(ApiException(409, 'chat is already finished'), l10n),
          'chat is already finished',
        );
      });

      test('the wait is rounded up to whole minutes', () {
        String wait(int? seconds) => describeError(
          AuthFailure(
            AuthFailureKind.tooManyAttempts,
            retryAfterSeconds: seconds,
          ),
          l10n,
        );
        expect(wait(1), l10n.authTooManyAttemptsMinutes(1));
        expect(wait(61), l10n.authTooManyAttemptsMinutes(2));
        expect(wait(3600), l10n.authTooManyAttemptsMinutes(60));
        expect(wait(0), l10n.authTooManyAttempts);
        expect(wait(null), l10n.authTooManyAttempts);
      });

      test('a 401 elsewhere says the user was signed out', () {
        expect(
          describeError(ApiException(401, 'not authenticated'), l10n),
          l10n.authSessionExpired,
        );
        expect(
          describeError(ApiException(404, 'not found'), l10n),
          'not found',
        );
      });
    });
  }

  test('the waits read naturally', () {
    expect(
      AppLocalizationsEn().authTooManyAttemptsMinutes(2),
      'Too many attempts. Please try again in 2 minutes.',
    );
    expect(
      AppLocalizationsDe().authTooManyAttemptsMinutes(1),
      'Zu viele Versuche. Bitte versuche es in einer Minute erneut.',
    );
  });
}
