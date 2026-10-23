import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

/// User-facing texts follow the self-help framing (ADR 0004): no therapy or
/// treatment claims, no conditions named, and nothing that ties a
/// self-hosted server to one operator or AI provider.

/// Words that make a medical claim or name a condition.
final _claims = {
  'en': RegExp(
    r'\b(therap\w*|treat\w*|cure[sd]?|heal(s|ed|ing)?|clinical\w*|diagnos\w*|'
    r'ptsd|trauma\w*|disorder\w*|insomnia|symptom\w*|patient\w*)\b',
    caseSensitive: false,
  ),
  'de': RegExp(
    r'\b(therap\w*|behandel\w*|behandlung\w*|heil(t|en|ung)|klinisch\w*|'
    r'diagnos\w*|ptbs|trauma\w*|störung\w*|schlafstörung\w*|symptom\w*|'
    r'patient\w*)\b',
    caseSensitive: false,
  ),
};

/// The disclaimers say what the app is NOT ("not therapy", "does not
/// diagnose or treat"); those words are allowed there and nowhere else.
const _disclaimers = {
  'settingsTagline',
  'onboardingScienceBody',
  'learnStudyExcerpt',
};

/// Names and claims that belong to one deployment, not to every server.
final _operatorBound = RegExp(
  'scaleway|mistral|langfuse|supabase|openai|redreamai|info@|osnabrück|'
  'gdpr.compliant|dsgvo.konform|european services|europäische dienste',
  caseSensitive: false,
);

Map<String, String> _texts(String lang) {
  final arb =
      jsonDecode(File('lib/l10n/app_$lang.arb').readAsStringSync())
          as Map<String, dynamic>;
  return {
    for (final MapEntry(:key, :value) in arb.entries)
      if (!key.startsWith('@') && value is String) key: value,
  };
}

void main() {
  for (final lang in ['en', 'de']) {
    group(lang, () {
      final texts = _texts(lang);

      test('no therapy or treatment claims outside the disclaimers', () {
        final hits = [
          for (final MapEntry(:key, :value) in texts.entries)
            if (!_disclaimers.contains(key))
              for (final m in _claims[lang]!.allMatches(value))
                '$key: "${m[0]}"',
        ];
        expect(hits, isEmpty);
      });

      test('the disclaimers say "not", and still exist', () {
        for (final key in _disclaimers) {
          expect(texts[key], isNotNull, reason: key);
          expect(
            texts[key],
            matches(RegExp(r'\b(not|keine?)\b', caseSensitive: false)),
            reason: key,
          );
        }
      });

      test('nothing names one operator or AI provider', () {
        final hits = [
          for (final MapEntry(:key, :value) in texts.entries)
            if (_operatorBound.hasMatch(value)) key,
        ];
        expect(hits, isEmpty);
      });
    });
  }
}
