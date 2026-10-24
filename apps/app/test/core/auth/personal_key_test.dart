import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:redream_app/core/auth/personal_key.dart';
import 'package:redream_app/core/storage/local_store.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  const key = 'Q2xhdWRlLXBlcnNvbmFsLWtleS0xMjM0NTY3ODkwYWJj';

  test('reads the key from the link make start prints', () {
    expect(PersonalKey.parse('key=$key'), key);
    expect(PersonalKey.parse('key=$key&other=1'), key);
    expect(PersonalKey.parse(''), isNull);
    expect(PersonalKey.parse('/'), isNull);
    expect(PersonalKey.parse('key='), isNull);
    expect(PersonalKey.parse('key=short'), isNull);
    expect(PersonalKey.parse('key=not a key<script>'), isNull);
    expect(PersonalKey.parse('key=%zz'), isNull);
  });

  test(
    'keeps the key across starts; a link without one changes nothing',
    () async {
      FlutterSecureStorage.setMockInitialValues({});
      SharedPreferences.setMockInitialValues({});
      final store = await LocalStore.open();

      expect(await PersonalKey(store).takeFrom('key=$key'), isTrue);
      expect(PersonalKey(store).value, key);

      expect(await PersonalKey(store).takeFrom(''), isFalse);
      expect(PersonalKey(store).value, key);

      expect(await PersonalKey(store).takeFrom('key=${key}X'), isTrue);
      expect(PersonalKey(store).value, '${key}X');
    },
  );
}
