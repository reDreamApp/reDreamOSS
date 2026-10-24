import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

/// User-facing texts follow the self-help framing (ADR 0004): no therapy or
/// treatment claims, no effect promises, no conditions named, and nothing
/// that ties a self-hosted server to one operator or AI provider.

/// Medical claims, effect promises and named conditions. German compounds
/// ("Psychotherapie", "Angststörung", "geheilt") match without a leading
/// word boundary; "Trauma" stays whole-word, "Traum" is the app's subject.
final _claims = {
  'en': RegExp(
    r'\b(therap\w*|treat\w*|cure[sd]?|heal(s|ed|ing)?|clinical\w*|'
    r'diagnos\w*|ptsd|trauma\w*|disorder\w*|insomnia|symptom\w*|'
    r'patient\w*|reduc\w*|reliev\w*|alleviat\w*|proven|effective\w*|'
    r'efficacy|detect\w*|monitor\w*|irt|sleep (better|calm\w*))\b',
    caseSensitive: false,
  ),
  'de': RegExp(
    '(therap|störung|behandl|heilt|heilung|geheilt|klinisch|diagnos|'
    'ptbs|symptom|patient|linder|reduzier|wirksam|nachgewiesen|'
    'erkennt|überwach|schlaflos|insomnie|wieder ruhig schl)|'
    r'\btrauma(ta|tisch\w*)?\b',
    caseSensitive: false,
    unicode: true,
  ),
};

/// The disclaimers say what the app is NOT; exactly these phrases are
/// taken out before checking, the rest of the sentence is checked too.
final _negations = {
  'en': RegExp(
    'is not therapy|does not (diagnose or )?treat any condition|'
    'a doctor or psychotherapist',
    caseSensitive: false,
  ),
  'de': RegExp(
    'keine Therapie|(diagnostiziert und )?behandelt keine Erkrankung(en)?|'
    'psychotherapeutischen Fachperson',
    caseSensitive: false,
  ),
};

/// Where the disclaimers live; they must keep saying "not".
const _disclaimers = {
  'settingsTagline',
  'onboardingScienceBody',
  'learnStudyExcerpt',
};

/// Names and claims that belong to one deployment, not to every server.
final _operatorBound = RegExp(
  'scaleway|mistral|langfuse|supabase|openai|redreamai|info@|osnabrück|'
  'gdpr.compliant|dsgvo.konform|european services|europäische dienste|'
  r'\bour (study|servers?)\b|\bunsere (studie|server)\b|france|frankreich|'
  r'\b30 (days|tage)\b',
  caseSensitive: false,
  unicode: true,
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

/// The microphone text iOS shows (English only).
Map<String, String> _plistTexts() => {
  for (final path in ['ios/Runner/Info.plist'])
    path: RegExp(
      r'<key>NSMicrophoneUsageDescription</key>\s*<string>([^<]*)</string>',
    ).firstMatch(File(path).readAsStringSync())![1]!,
};

/// What a browser shows about the web app (English only).
Map<String, String> _webTexts() => {
  'web/index.html': RegExp(
    '<meta name="description" content="([^"]*)">',
  ).firstMatch(File('web/index.html').readAsStringSync())![1]!,
  'web/manifest.json':
      (jsonDecode(File('web/manifest.json').readAsStringSync())
              as Map<String, dynamic>)['description']
          as String,
};

List<String> _claimHits(Map<String, String> texts, String lang) => [
  for (final MapEntry(:key, :value) in texts.entries)
    for (final m in _claims[lang]!.allMatches(
      value.replaceAll(_negations[lang]!, ''),
    ))
      '$key: "${m[0]}"',
];

void main() {
  for (final lang in ['en', 'de']) {
    group(lang, () {
      final texts = _texts(lang);

      test('no therapy, treatment or effect claims', () {
        expect(_claimHits(texts, lang), isEmpty);
      });

      test('the disclaimers say "not", and still exist', () {
        for (final key in _disclaimers) {
          expect(texts[key], isNotNull, reason: key);
          expect(texts[key], matches(_negations[lang]!), reason: key);
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

  test('the microphone texts follow the same rules', () {
    final texts = _plistTexts();
    expect(texts, hasLength(1));
    expect(_claimHits(texts, 'en'), isEmpty);
    expect(texts.values.where(_operatorBound.hasMatch), isEmpty);
  });

  test('the web page texts follow the same rules', () {
    final texts = _webTexts();
    expect(texts, hasLength(2));
    expect(_claimHits(texts, 'en'), isEmpty);
    expect(texts.values.where(_operatorBound.hasMatch), isEmpty);
  });

  test('the checks catch what they are for', () {
    expect(
      _claimHits({'k': 'Reduces nightmares, clinically proven'}, 'en'),
      hasLength(3),
    );
    expect(
      _claimHits({'k': 'Hilft bei Angststörung und Psychotherapie'}, 'de'),
      hasLength(2),
    );
    expect(_claimHits({'k': 'Traumanalyse und Traumarbeit'}, 'de'), isEmpty);
    expect(
      _claimHits({'k': 'reDreamOSS is not therapy but helps with PTSD'}, 'en'),
      ['k: "PTSD"'],
    );
    expect(_operatorBound.hasMatch('Unsere Server in Frankreich'), isTrue);
  });
}
