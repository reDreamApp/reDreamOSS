import 'package:flutter/material.dart';

import 'package:redream_app/core/storage/local_store.dart';

/// Theme, language and the post-sign-up onboarding flag. Persisted locally.
class AppSettings extends ChangeNotifier {
  AppSettings(this._store, {required String platformLanguage})
    : _themeMode = _parseThemeMode(_store.getString(_themeKey)),
      _language = normalizeLanguage(
        _store.getString(_languageKey) ?? platformLanguage,
      );

  static const _themeKey = 'theme_mode';
  static const _languageKey = 'selected_language';
  static const _onboardingKey = 'pending_onboarding_user';
  static const _personalUserKey = 'personal_user';

  static const supportedLanguages = ['en', 'de'];

  final LocalStore _store;
  ThemeMode _themeMode;
  String _language;

  ThemeMode get themeMode => _themeMode;
  String get language => _language;
  Locale get locale => Locale(_language);

  bool isDark(BuildContext context) => switch (_themeMode) {
    ThemeMode.dark => true,
    ThemeMode.light => false,
    ThemeMode.system =>
      MediaQuery.platformBrightnessOf(context) == Brightness.dark,
  };

  Future<void> setThemeMode(ThemeMode mode) async {
    if (mode == _themeMode) return;
    _themeMode = mode;
    notifyListeners();
    await _store.setString(_themeKey, mode.name);
  }

  Future<void> setLanguage(String code) async {
    final normalized = normalizeLanguage(code);
    if (normalized == _language) return;
    _language = normalized;
    notifyListeners();
    await _store.setString(_languageKey, normalized);
  }

  bool needsOnboarding(String? userId) =>
      userId != null && _store.getString(_onboardingKey) == userId;

  Future<void> markOnboardingPending(String userId) async {
    await _store.setString(_onboardingKey, userId);
    notifyListeners();
  }

  /// True when [userId] is a personal server's user this app has not seen
  /// yet (the first start, or after the account was deleted): like a
  /// sign-up, it begins with onboarding.
  Future<bool> notePersonalUser(String userId) async {
    if (_store.getString(_personalUserKey) == userId) return false;
    await _store.setString(_personalUserKey, userId);
    return true;
  }

  Future<void> completeOnboarding() async {
    await _store.remove(_onboardingKey);
    notifyListeners();
  }

  static String normalizeLanguage(String code) =>
      code.toLowerCase().startsWith('de') ? 'de' : 'en';

  static ThemeMode _parseThemeMode(String? raw) => ThemeMode.values.firstWhere(
    (m) => m.name == raw,
    orElse: () => ThemeMode.system,
  );
}
