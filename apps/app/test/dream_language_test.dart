import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:redream_app/core/auth/auth_service.dart';
import 'package:redream_app/core/settings/app_settings.dart';
import 'package:redream_app/core/storage/local_store.dart';
import 'package:redream_app/features/journal/dream.dart';
import 'package:redream_app/features/journal/dream_service.dart';
import 'package:redream_app/features/journal/dream_store.dart';
import 'package:redream_app/features/journal/journal_state.dart';
import 'package:redream_app/features/journal/practice_audio_screen.dart';
import 'package:redream_app/l10n/generated/app_localizations.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'fake_api.dart';
import 'fake_auth.dart';

void main() {
  group('Dream.language', () {
    test('parses when the server sends it', () {
      final dream = Dream.fromJson(dreamJson(1, language: 'de'));
      expect(dream.language, 'de');
      expect(dream.toJson()['language'], 'de');
      expect(Dream.fromJson(dream.toJson()).language, 'de');
      expect(dream.copyWith(title: 'Other').language, 'de');
    });

    test('is null for old caches and servers without the field', () {
      final json = dreamJson(1)..remove('language');
      expect(json.containsKey('language'), isFalse);
      expect(Dream.fromJson(json).language, isNull);
    });

    test("the rehearsal asks in the dream's own language first", () {
      Dream dream({String? language, String? rehearsal}) => Dream.fromJson({
        ...dreamJson(1),
        'language': language,
        'rehearsal_language': rehearsal,
      });
      expect(dream(language: 'de').rehearsalLanguageOr('en'), 'de');
      expect(
        dream(language: 'de', rehearsal: 'en').rehearsalLanguageOr('en'),
        'de',
      );
      expect(dream(rehearsal: 'de').rehearsalLanguageOr('en'), 'de');
      expect(dream().rehearsalLanguageOr('en'), 'en');
    });
  });

  group('manual dreams', () {
    const input = DreamInput(
      title: 'Falling',
      originalDream: 'Ich bin gefallen.',
      isNightmare: false,
      language: 'de',
    );

    test('an offline draft keeps its language; old drafts still parse', () {
      final draft = DreamDraft(
        localId: -1,
        input: input,
        createdAt: DateTime.utc(2025, 11, 3),
        lastEdited: DateTime.utc(2025, 11, 3),
      );
      final restored = DreamDraft.fromJson(draft.toJson());
      expect(restored.input.language, 'de');
      expect(restored.toDream().language, 'de');

      final old = draft.toJson()..remove('language');
      expect(DreamDraft.fromJson(old).input.language, isNull);
    });

    test('create sends the language, update leaves it out', () async {
      final bodies = <String, Map<String, dynamic>>{};
      final api = FakeApi()
        ..on('POST /api/v1/dreams', (_, body) {
          bodies['create'] = body;
          return dreamJson(5);
        })
        ..on('PUT /api/v1/dreams/{id}', (_, body) {
          bodies['update'] = body;
          return dreamJson(5);
        });
      final service = DreamService(api.client());

      await service.create(input);
      await service.update(5, input);

      expect(bodies['create']!['language'], 'de');
      expect(bodies['update']!.containsKey('language'), isFalse);
    });

    test('a dream without a language sends none', () {
      const plain = DreamInput(
        title: 't',
        originalDream: 'd',
        isNightmare: false,
      );
      expect(plain.toJson().containsKey('language'), isFalse);
    });
  });

  test("the rehearsal request carries the dream's language", () async {
    Map<String, dynamic>? body;
    final api = FakeApi()
      ..on('POST /api/v1/dreams/{id}/rehearsal', (_, b) {
        body = b;
        return dreamJson(1);
      });
    final dream = Dream.fromJson(dreamJson(1, language: 'de'));

    await DreamService(
      api.client(),
    ).generateRehearsal(dream.id, language: dream.rehearsalLanguageOr('en'));

    expect(body, {'language': 'de'});
  });

  group('the audio rehearsal screen', () {
    late Directory support;
    const pathProvider = MethodChannel('plugins.flutter.io/path_provider');

    setUp(() {
      support = Directory.systemTemp.createTempSync('redream_audio_test');
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(pathProvider, (_) async => support.path);
    });

    tearDown(() {
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(pathProvider, null);
      support.deleteSync(recursive: true);
    });

    /// Opens the screen for [dream] with the app set to English and returns
    /// the bodies of the rehearsal requests it made.
    Future<List<Map<String, dynamic>>> open(
      WidgetTester tester,
      Map<String, dynamic> dream,
    ) async {
      final requests = <Map<String, dynamic>>[];
      final generated = {
        ...dream,
        'rehearsal_audio_url': 'http://fake/audio-de.mp3',
        'rehearsal_language': 'de',
      };
      final api = FakeApi()
        ..on('GET /api/v1/dreams/{id}', (_, _) => dream)
        ..on('POST /api/v1/dreams/{id}/rehearsal', (_, body) {
          requests.add(body);
          return generated;
        })
        ..on('GET /api/v1/dreams/{id}/rehearsal-url', (_, _) => generated);
      FlutterSecureStorage.setMockInitialValues({});
      SharedPreferences.setMockInitialValues({});
      final store = await LocalStore.open();
      await tester.pumpWidget(
        MultiProvider(
          providers: [
            ChangeNotifierProvider<AuthService>.value(
              value: FakeAuth(userId: 'user-1'),
            ),
            ChangeNotifierProvider(
              create: (_) => AppSettings(store, platformLanguage: 'en'),
            ),
            ChangeNotifierProvider(
              create: (_) => JournalState(
                DreamService(api.client(token: 'test')),
                DreamStore(store, 'user-1'),
              ),
            ),
          ],
          child: MaterialApp(
            localizationsDelegates: AppLocalizations.localizationsDelegates,
            supportedLocales: AppLocalizations.supportedLocales,
            home: PracticeAudioScreen(dream: Dream.fromJson(dream)),
          ),
        ),
      );
      // The player itself has no platform here; only the requests matter.
      await tester.runAsync(() async {
        for (var i = 0; i < 20 && requests.isEmpty; i++) {
          await tester.pump();
          await Future<void>.delayed(const Duration(milliseconds: 20));
        }
      });
      return requests;
    }

    testWidgets("asks in the dream's language, not the app's", (tester) async {
      final requests = await open(tester, dreamJson(1, language: 'de'));
      expect(requests, [
        {'language': 'de'},
      ]);
    });

    testWidgets('remakes audio that is in another language', (tester) async {
      final requests = await open(tester, {
        ...dreamJson(1, language: 'de'),
        'rehearsal_audio_url': 'http://fake/audio-en.mp3',
        'rehearsal_language': 'en',
      });
      expect(requests, [
        {'language': 'de'},
      ]);
    });
  });
}
