import 'dart:convert';

import 'package:cryptography/cryptography.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:redream_app/core/storage/local_cipher.dart';
import 'package:redream_app/core/storage/local_store.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUp(() {
    FlutterSecureStorage.setMockInitialValues({});
    SharedPreferences.setMockInitialValues({});
  });

  test(
    'migrates plaintext drafts, preserves data and reopens ciphertext',
    () async {
      const key = 'dream_drafts:user-1';
      SharedPreferences.setMockInitialValues({
        key: '[{"text":"private dream"}]',
      });
      final store = await LocalStore.open();
      final prefs = await SharedPreferences.getInstance();
      final encrypted = prefs.getString(key)!;
      expect(encrypted, startsWith(LocalCipher.prefix));
      expect(encrypted, isNot(contains('private dream')));
      expect(store.getJson(key), [
        {'text': 'private dream'},
      ]);
      expect((await LocalStore.open()).getJson(key), store.getJson(key));
      await store.setJson(key, [
        {'text': 'new private dream'},
      ]);
      expect((await LocalStore.open()).getJson(key), [
        {'text': 'new private dream'},
      ]);
      expect(prefs.getString(key), isNot(contains('new private dream')));
      await store.remove(key);
      expect(prefs.containsKey(key), isFalse);
      expect(store.getJson(key), isNull);
    },
  );

  test(
    'cipher authenticates content and account context with fresh nonces',
    () async {
      final cipher = await LocalCipher.open();
      final first = await cipher.encrypt(
        utf8.encode('private'),
        'dreams_cache:user-1',
      );
      final second = await cipher.encrypt(
        utf8.encode('private'),
        'dreams_cache:user-1',
      );
      expect(first, isNot(second));
      expect(
        utf8.decode(await cipher.decrypt(first, 'dreams_cache:user-1')),
        'private',
      );
      await expectLater(
        cipher.decrypt(first, 'dreams_cache:user-2'),
        throwsA(isA<SecretBoxAuthenticationError>()),
      );
      final bytes = base64Decode(first.substring(LocalCipher.prefix.length));
      bytes[12] ^= 1;
      await expectLater(
        cipher.decrypt(
          '${LocalCipher.prefix}${base64Encode(bytes)}',
          'dreams_cache:user-1',
        ),
        throwsA(isA<SecretBoxAuthenticationError>()),
      );
    },
  );
}
