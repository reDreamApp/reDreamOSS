import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:redream_app/core/auth/session_store.dart';
import 'package:redream_app/core/storage/local_cipher.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'dropped_writes.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUp(() {
    FlutterSecureStorage.setMockInitialValues({});
    SharedPreferences.setMockInitialValues({});
  });

  final session = StoredSession(
    token: 'rdo_abc',
    userId: 'user-1',
    email: 'me@example.com',
    server: 'https://dreams.example',
    expiresAt: DateTime.utc(2026, 11, 22),
  );

  test('writes, reads back and clears one session', () async {
    final store = SessionStore();
    expect(await store.read(), isNull);

    await store.write(session);
    final read = (await store.read())!;
    expect(read.token, 'rdo_abc');
    expect(read.userId, 'user-1');
    expect(read.email, 'me@example.com');
    expect(read.server, 'https://dreams.example');
    expect(read.expiresAt, DateTime.utc(2026, 11, 22));

    await store.clear();
    expect(await store.read(), isNull);
    expect(await secureStorage.readAll(), isEmpty);
  });

  test('a corrupt value reads as no session and is deleted', () async {
    for (final raw in ['{not json', '[1]', '{"token": "rdo_abc"}']) {
      FlutterSecureStorage.setMockInitialValues({'redream-session-v1': raw});
      expect(await SessionStore().read(), isNull, reason: raw);
      expect(
        await secureStorage.containsKey(key: 'redream-session-v1'),
        isFalse,
        reason: raw,
      );
    }
  });

  test('the origin of a base URL keys the session', () {
    expect(
      StoredSession.originOf('https://dreams.example:8443/base/'),
      'https://dreams.example:8443',
    );
    // Not a web address: no crash before the first frame.
    for (final url in ['localhost:8000', 'dreams.example', 'http:///x', '']) {
      expect(StoredSession.originOf(url), url);
    }
  });

  test('a write the storage does not keep throws an Exception', () async {
    FlutterSecureStorage.setMockInitialValues(DroppedWrites());
    await expectLater(
      SessionStore().write(session),
      throwsA(isA<SessionWriteException>()),
    );
  });

  test(
    'removes old Supabase keys from both stores and keeps the rest',
    () async {
      FlutterSecureStorage.setMockInitialValues({
        'sb-project-auth-token': '{"refresh_token":"x"}',
        'pkce:supabase.auth.token-code-verifier': 'verifier',
        'supabase.auth.token-code-verifier': 'verifier',
        'redream-local-key-v1': 'key',
        'redream-session-v1': 'session',
      });
      SharedPreferences.setMockInitialValues({
        'sb-project-auth-token': 'legacy',
        'pkce:other': 'verifier',
        'supabase.auth.token-code-verifier': 'verifier',
        'theme_mode': 'dark',
      });

      await SessionStore.removeLegacy();

      expect((await secureStorage.readAll()).keys.toSet(), {
        'redream-local-key-v1',
        'redream-session-v1',
      });
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.containsKey('sb-project-auth-token'), isFalse);
      expect(prefs.containsKey('pkce:other'), isFalse);
      expect(prefs.containsKey('supabase.auth.token-code-verifier'), isFalse);
      expect(prefs.getString('theme_mode'), 'dark');
    },
  );

  test('the legacy sweep runs once', () async {
    await SessionStore.removeLegacy();
    await secureStorage.write(key: 'sb-late-auth-token', value: 'x');

    await SessionStore.removeLegacy();

    expect(await secureStorage.read(key: 'sb-late-auth-token'), 'x');
  });
}
