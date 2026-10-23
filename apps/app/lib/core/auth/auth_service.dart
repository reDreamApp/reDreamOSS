import 'package:flutter/foundation.dart';

/// Session state the UI listens to. Implemented by `TokenAuthService` in
/// production and by fakes in tests.
abstract class AuthService extends ChangeNotifier {
  String? get accessToken;
  String? get userId;
  String? get email;
  bool get isSignedIn => userId != null;

  Future<void> signIn({required String email, required String password});
  Future<void> signUp({
    required String email,
    required String password,
    String? inviteCode,
  });

  /// Always ends the local session, even when the server cannot be reached.
  Future<void> signOut();

  /// A request carrying [usedToken] got a 401. True when the same user holds
  /// a different token now (retry with it). A session that is not
  /// [usedToken]'s is kept; [usedToken]'s own session is cleared.
  Future<bool> onUnauthorized(String? usedToken);
  Future<void> updateEmail({
    required String currentPassword,
    required String newEmail,
  });
  Future<void> updatePassword({
    required String currentPassword,
    required String newPassword,
  });
}

enum AuthFailureKind {
  invalidCredentials,
  emailTaken,
  registrationClosed,
  inviteInvalid,
  wrongPassword,
  tooManyAttempts,
  invalidInput,
  sessionExpired,
  other,
}

/// A refused auth request, already told apart for the UI.
class AuthFailure implements Exception {
  const AuthFailure(this.kind, {this.retryAfterSeconds});

  final AuthFailureKind kind;

  /// Only for [AuthFailureKind.tooManyAttempts], when the server says.
  final int? retryAfterSeconds;

  @override
  String toString() => 'AuthFailure(${kind.name})';
}
