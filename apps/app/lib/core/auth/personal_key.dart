import 'package:redream_app/core/storage/local_store.dart';

/// The key of a personal server. `make start` opens the browser at
/// `http://localhost:8000/#key=<key>`: the fragment never reaches the server
/// or its logs. The app keeps the key and sends it with each personal
/// sign-in; without it the server lets no one in.
class PersonalKey {
  PersonalKey(this._store);

  static const _storeKey = 'personal_key';
  static final _shape = RegExp(r'^[A-Za-z0-9_-]{16,256}$');

  final LocalStore _store;

  String? get value => _store.getString(_storeKey);

  /// Keeps the key in [fragment] (`key=...`), if there is one; true then.
  Future<bool> takeFrom(String fragment) async {
    final key = parse(fragment);
    if (key == null) return false;
    await _store.setString(_storeKey, key);
    return true;
  }

  static String? parse(String fragment) {
    final Map<String, String> params;
    try {
      params = Uri.splitQueryString(fragment);
      // A broken %-escape: Uri throws ArgumentError, not FormatException.
      // ignore: avoid_catching_errors
    } on ArgumentError catch (_) {
      return null;
    } on FormatException catch (_) {
      return null;
    }
    final key = params['key'];
    return key != null && _shape.hasMatch(key) ? key : null;
  }
}
