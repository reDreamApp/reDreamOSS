import 'dart:convert';

import 'package:redream_app/core/platform/platform_features.dart';
import 'package:redream_app/core/storage/local_cipher.dart';
import 'package:redream_app/core/storage/secure_audio_cache.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Small key/value store for settings, caches and offline queues.
class LocalStore {
  LocalStore._(this._prefs, this._cipher);

  final LocalCipher _cipher;
  final Map<String, String> _sensitive = {};
  static bool _private(String key) =>
      key.startsWith('dreams_cache:') ||
      key.startsWith('dream_drafts:') ||
      key.startsWith('pending_practices:');

  static Future<LocalStore> open() async {
    final store = LocalStore._(
      await SharedPreferences.getInstance(),
      await LocalCipher.open(),
    );
    for (final key in store._prefs.getKeys().where(_private)) {
      final raw = store._prefs.getString(key);
      if (raw == null) continue;
      if (raw.startsWith(LocalCipher.prefix)) {
        store._sensitive[key] = utf8.decode(
          await store._cipher.decrypt(raw, key),
        );
      } else {
        // Persist ciphertext successfully before replacing the legacy value.
        await store.setString(key, raw);
      }
    }
    return store;
  }

  final SharedPreferences _prefs;

  String? getString(String key) =>
      _private(key) ? _sensitive[key] : _prefs.getString(key);
  bool? getBool(String key) => _prefs.getBool(key);
  int? getInt(String key) => _prefs.getInt(key);

  Future<void> setString(String key, String value) async {
    if (_private(key)) {
      final encrypted = await _cipher.encrypt(utf8.encode(value), key);
      if (!await _prefs.setString(key, encrypted)) {
        throw StateError('Unable to save local data');
      }
      _sensitive[key] = value;
    } else {
      await _prefs.setString(key, value);
    }
  }

  Future<void> setBool(String key, {required bool value}) =>
      _prefs.setBool(key, value);
  Future<void> setInt(String key, int value) => _prefs.setInt(key, value);
  Future<void> remove(String key) async {
    await _prefs.remove(key);
    _sensitive.remove(key);
  }

  Object? getJson(String key) {
    final raw = getString(key);
    if (raw == null) return null;
    try {
      return jsonDecode(raw);
    } on FormatException {
      return null;
    }
  }

  Future<void> setJson(String key, Object? value) =>
      value == null ? remove(key) : setString(key, jsonEncode(value));

  /// Removes every key that belongs to [userId] (keys end in `:<userId>`).
  Future<void> clearUser(String userId) async {
    if (PlatformFeatures.offlineAudio) await SecureAudioCache.clearUser(userId);
    for (final key in _prefs.getKeys().where((k) => k.endsWith(':$userId'))) {
      await remove(key);
    }
  }
}
