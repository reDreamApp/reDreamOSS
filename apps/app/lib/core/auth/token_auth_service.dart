import 'package:redream_app/core/api/api_client.dart';
import 'package:redream_app/core/api/api_exception.dart';
import 'package:redream_app/core/auth/auth_service.dart';
import 'package:redream_app/core/auth/session_store.dart';

/// Email/password sign-in against the app's own server (`/api/v1/auth`).
/// The session is held in memory, so the getters stay synchronous, and kept
/// in [SessionStore] across launches.
class TokenAuthService extends AuthService {
  TokenAuthService(
    this._api,
    this._store,
    StoredSession? initial,
    String serverOrigin,
  ) : _server = serverOrigin,
      _session = initial?.server == serverOrigin ? initial : null {
    // A token is never sent to a host it was not issued by.
    if (initial != null && _session == null) _store.clear().ignore();
    if (_session case final session?) _owners[session.token] = session.userId;
  }

  final ApiClient _api;
  final SessionStore _store;
  final String _server;
  StoredSession? _session;

  /// The user behind every token held since launch, so a late 401 is only
  /// retried for the same person (a few entries at most).
  final _owners = <String, String>{};

  static const _logoutTimeout = Duration(seconds: 5);

  @override
  String? get accessToken => _session?.token;

  @override
  String? get userId => _session?.userId;

  @override
  String? get email => _session?.email;

  @override
  Future<void> signIn({required String email, required String password}) =>
      _start('/api/v1/auth/login', {
        'email': email,
        'password': password,
      }, login: true);

  @override
  Future<void> signUp({
    required String email,
    required String password,
    String? inviteCode,
  }) {
    final code = inviteCode?.trim() ?? '';
    return _start('/api/v1/auth/register', {
      'email': email,
      'password': password,
      if (code.isNotEmpty) 'invite_code': code,
    });
  }

  @override
  Future<void> signOut() async {
    if (_session != null) {
      // Best effort: after account deletion the token is already revoked.
      try {
        await _api.post('/api/v1/auth/logout', null, _logoutTimeout);
      } on Exception catch (_) {}
    }
    await _clear();
  }

  /// A request that started before a sign-out and a new sign-in is retried
  /// only when the same user signed in again, never under someone else's
  /// account; the newer session is left alone either way.
  @override
  Future<bool> onUnauthorized(String? usedToken) async {
    final session = _session;
    if (session == null) return false;
    if (session.token != usedToken) {
      return usedToken != null && _owners[usedToken] == session.userId;
    }
    await _clear();
    return false;
  }

  /// The server keeps the token that made the change and signs out the rest.
  @override
  Future<void> updatePassword({
    required String currentPassword,
    required String newPassword,
  }) => _call(
    () => _api.post('/api/v1/auth/password', {
      'current_password': currentPassword,
      'new_password': newPassword,
    }),
  );

  @override
  Future<void> updateEmail({
    required String currentPassword,
    required String newEmail,
  }) async {
    final user = await _call(
      () => _api.post('/api/v1/auth/email', {
        'current_password': currentPassword,
        'new_email': newEmail,
      }),
    );
    final session = _session;
    if (session == null) return;
    await _save(session.withEmail(user['email'] as String));
  }

  /// Checks the stored token at startup. Only a 401 ends the session: offline
  /// the journal and chat history keep working with it.
  Future<void> validate() async {
    final token = accessToken;
    if (token == null) return;
    try {
      await _api.post('/api/v1/auth/session/validate');
    } on ApiException catch (e) {
      if (e.isUnauthorized && accessToken == token) await _clear();
    } on Exception catch (_) {}
  }

  Future<void> _start(
    String path,
    Map<String, Object?> body, {
    bool login = false,
  }) async {
    final res = await _call(() => _api.post(path, body), login: login);
    final user = res['user'] as Map<String, dynamic>;
    await _save(
      StoredSession(
        token: res['token'] as String,
        userId: user['id'] as String,
        email: user['email'] as String,
        server: _server,
        expiresAt: DateTime.tryParse(res['expires_at'] as String? ?? ''),
      ),
    );
  }

  Future<void> _save(StoredSession session) async {
    await _store.write(session);
    _owners[session.token] = session.userId;
    _session = session;
    notifyListeners();
  }

  Future<void> _clear() async {
    _session = null;
    notifyListeners();
    await _store.clear();
  }

  static Future<T> _call<T>(
    Future<T> Function() request, {
    bool login = false,
  }) async {
    try {
      return await request();
    } on ApiException catch (e) {
      throw _failure(e, login: login);
    }
  }

  /// The one place that reads the server's auth answers: status first, the
  /// `detail` text only to tell the 403s apart.
  static AuthFailure _failure(ApiException e, {required bool login}) {
    final kind = switch (e.statusCode) {
      401 when login => AuthFailureKind.invalidCredentials,
      401 => AuthFailureKind.sessionExpired,
      403 => switch (e.message) {
        'registration closed' => AuthFailureKind.registrationClosed,
        'invalid invite code' => AuthFailureKind.inviteInvalid,
        'wrong password' => AuthFailureKind.wrongPassword,
        _ => AuthFailureKind.other,
      },
      409 => AuthFailureKind.emailTaken,
      422 => AuthFailureKind.invalidInput,
      429 => AuthFailureKind.tooManyAttempts,
      _ => AuthFailureKind.other,
    };
    return AuthFailure(
      kind,
      retryAfterSeconds: kind == AuthFailureKind.tooManyAttempts
          ? e.retryAfterSeconds
          : null,
    );
  }
}
