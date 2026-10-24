import 'package:redream_app/core/api/api_exception.dart';
import 'package:redream_app/core/auth/auth_service.dart';
import 'package:redream_app/core/server/server_config.dart';
import 'package:redream_app/l10n/generated/app_localizations.dart';

/// One user-facing sentence for any failure the services can throw.
String describeError(Object error, AppLocalizations l10n) => switch (error) {
  NetworkException() => l10n.errorOffline,
  AuthFailure() => _authMessage(error, l10n),
  ServerFailure(:final kind) => _serverMessage(kind, l10n),
  ApiException(isDailyLimit: true) => l10n.dailyLimitBody,
  ApiException(isVoiceUnavailable: true) => l10n.voiceUnavailable,
  ApiException(isRateLimited: true) => l10n.errorRateLimited,
  // The 401 hook has already ended the session.
  ApiException(isUnauthorized: true) => l10n.authSessionExpired,
  ApiException(isServerError: true) => l10n.errorServer,
  ApiException(:final message) when message.isNotEmpty => message,
  _ => l10n.errorUnknown,
};

bool isOffline(Object error) => error is NetworkException;

String _authMessage(AuthFailure failure, AppLocalizations l10n) =>
    switch (failure.kind) {
      AuthFailureKind.invalidCredentials => l10n.signInError,
      AuthFailureKind.emailTaken => l10n.authEmailTaken,
      AuthFailureKind.registrationClosed => l10n.authRegistrationClosed,
      AuthFailureKind.inviteInvalid => l10n.authInviteInvalid,
      AuthFailureKind.wrongPassword => l10n.changePasswordWrongCurrent,
      AuthFailureKind.tooManyAttempts => switch (failure.retryAfterSeconds) {
        final seconds? when seconds > 0 => l10n.authTooManyAttemptsMinutes(
          (seconds / 60).ceil(),
        ),
        _ => l10n.authTooManyAttempts,
      },
      AuthFailureKind.invalidInput => l10n.authInvalidInput,
      AuthFailureKind.sessionExpired => l10n.authSessionExpired,
      AuthFailureKind.personalKey => l10n.personalKeyNeeded,
      AuthFailureKind.other => l10n.errorUnknown,
    };

String _serverMessage(ServerFailureKind kind, AppLocalizations l10n) =>
    switch (kind) {
      ServerFailureKind.invalidAddress => l10n.serverInvalidAddress,
      ServerFailureKind.httpsRequired => l10n.serverHttpsRequired,
      ServerFailureKind.unreachable => l10n.serverUnreachable,
      ServerFailureKind.notAServer => l10n.serverNotFound,
      ServerFailureKind.appOutdated => l10n.serverAppOutdated,
      ServerFailureKind.serverOutdated => l10n.serverOutdated,
    };
