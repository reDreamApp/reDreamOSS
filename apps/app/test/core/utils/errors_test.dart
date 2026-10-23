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
          AuthFailureKind.other: l10n.errorUnknown,
        };
        expect(expected.keys, unorderedEquals(AuthFailureKind.values));
        for (final MapEntry(:key, :value) in expected.entries) {
          expect(describeError(AuthFailure(key), l10n), value, reason: '$key');
        }
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
