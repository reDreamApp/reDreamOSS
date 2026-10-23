import 'dart:convert';

import 'package:redream_app/core/storage/local_cipher.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// A sign-in to one server. [expiresAt] is informational only: the server
/// slides it on every use, so a 401 is what ends a session, not the clock.
class StoredSession {
  const StoredSession({
    required this.token,
    required this.userId,
    required this.email,
    required this.server,
    this.expiresAt,
  });

  factory StoredSession.fromJson(Map<String, dynamic> json) {
    final (token, userId, email, server) = (
      json['token'],
      json['userId'],
      json['email'],
      json['server'],
    );
    if (token is! String ||
        userId is! String ||
        email is! String ||
        server is! String) {
      throw const FormatException('Invalid stored session');
    }
    final expiresAt = json['expiresAt'];
    return StoredSession(
      token: token,
      userId: userId,
      email: email,
      server: server,
      expiresAt: expiresAt is String ? DateTime.tryParse(expiresAt) : null,
    );
  }

  final String token;
  final String userId;
  final String email;

  /// Address of the server the token belongs to (as ServerConfig keeps
  /// it); it is never sent anywhere else.
  final String server;
  final DateTime? expiresAt;

  StoredSession withEmail(String email) => StoredSession(
    token: token,
    userId: userId,
    email: email,
    server: server,
    expiresAt: expiresAt,
  );

  Map<String, Object?> toJson() => {
    'token': token,
    'expiresAt': expiresAt?.toIso8601String(),
    'userId': userId,
    'email': email,
    'server': server,
  };
}

/// The session as one JSON value in [secureStorage].
class SessionStore {
  static const _key = 'redream-session-v1';
  static const _legacySwept = 'legacy_session_keys_removed';

  /// The stored session; a value that cannot be read is deleted.
  Future<StoredSession?> read() async {
    final raw = await secureStorage.read(key: _key);
    if (raw == null) return null;
    try {
      final decoded = jsonDecode(raw);
      if (decoded is Map<String, dynamic>) {
        return StoredSession.fromJson(decoded);
      }
    } on FormatException catch (_) {}
    await clear();
    return null;
  }

  Future<void> write(StoredSession session) async {
    final raw = jsonEncode(session.toJson());
    await secureStorage.write(key: _key, value: raw);
    if (await secureStorage.read(key: _key) != raw) {
      throw const SessionWriteException();
    }
  }

  Future<void> clear() => secureStorage.delete(key: _key);

  /// Once per install, removes what the former Supabase sign-in left behind
  /// ('sb-*-auth-token' sessions, 'pkce:*' and 'supabase.auth.token*'
  /// verifiers) in secure storage and SharedPreferences. Best effort: never
  /// throws.
  static Future<void> removeLegacy() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      if (prefs.getBool(_legacySwept) ?? false) return;
      final secure = await secureStorage.readAll();
      for (final key in secure.keys.where(_isLegacy).toList()) {
        await secureStorage.delete(key: key);
      }
      for (final key in prefs.getKeys().where(_isLegacy).toList()) {
        await prefs.remove(key);
      }
      await prefs.setBool(_legacySwept, true);
      // Anything at all: this runs before the first frame.
    } on Object catch (_) {}
  }

  static bool _isLegacy(String key) =>
      (key.startsWith('sb-') && key.endsWith('-auth-token')) ||
      key.startsWith('pkce:') ||
      key.startsWith('supabase.auth.token');
}

/// The secure storage did not keep the session; the screens show the
/// generic error for it.
class SessionWriteException implements Exception {
  const SessionWriteException();

  @override
  String toString() => 'SessionWriteException: unable to persist session';
}
