import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:redream_app/app.dart';
import 'package:redream_app/core/app_services.dart';
import 'package:redream_app/core/auth/auth_service.dart';
import 'package:redream_app/core/platform/platform_features.dart';
import 'package:redream_app/core/server/server_config.dart';
import 'package:redream_app/core/settings/app_settings.dart';
import 'package:redream_app/core/storage/local_store.dart';
import 'package:redream_app/core/theme/app_theme.dart';
import 'package:redream_app/core/widgets/brand_logo.dart';
import 'package:redream_app/core/widgets/theme_mode_toggle.dart';
import 'package:redream_app/features/chat/stage_indicator.dart';
import 'package:redream_app/features/chat/voice_recorder.dart';
import 'package:redream_app/features/consent/consent_service.dart';
import 'package:redream_app/features/journal/dream_detail_screen.dart';
import 'package:redream_app/features/reminders/notification_service.dart';
import 'package:redream_app/features/settings/profile.dart';
import 'package:redream_app/l10n/generated/app_localizations_de.dart';
import 'package:redream_app/l10n/generated/app_localizations_en.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'fake_api.dart';
import 'fake_auth.dart';

final _en = AppLocalizationsEn();

class _FakeRecorder implements IVoiceRecorder {
  bool cancelled = false;

  @override
  Future<bool> hasPermission() async => true;

  @override
  Future<void> start() async {}

  @override
  Future<VoiceRecording?> stop() async => VoiceRecording(
    bytes: Uint8List.fromList([1, 2, 3]),
    fileName: 'dream_recording.wav',
  );

  @override
  Future<void> cancel() async => cancelled = true;

  @override
  Future<void> dispose() async {}
}

/// The app on the fake server at `http://fake`, connected unless
/// [connected] is false; [instance] is what the server says about itself.
Future<(Widget, FakeApi, AppSettings)> _app({
  FakeAuth? auth,
  bool consentRequired = false,
  IVoiceRecorder? recorder,
  bool connected = true,
  Map<String, Object?>? instance,
  String? pageOrigin,
}) async {
  var needsConsent = consentRequired;
  FlutterSecureStorage.setMockInitialValues({});
  SharedPreferences.setMockInitialValues({});
  final store = await LocalStore.open();
  final settings = AppSettings(store, platformLanguage: 'en');
  final api = FakeApi()
    ..on('GET /api/v1/instance', (_, _) => instance ?? instanceJson())
    ..on(
      'GET /api/v1/dreams',
      (_, _) => [
        dreamJson(
          1,
          current: true,
          practices: ['2025-11-03T10:00:00Z'],
          sessionId: '3',
        ),
        dreamJson(
          2,
          title: 'Flying',
          nightmare: false,
          rewritten: null,
          intensity: null,
        ),
      ],
    )
    ..on('GET /api/v1/progress/stats', (_, _) => statsJson())
    ..on(
      'GET /api/v1/users/me/consent',
      (_, _) => consentJson(required: needsConsent),
    )
    ..on('POST /api/v1/users/me/consent', (_, _) {
      needsConsent = false;
      return null;
    })
    ..on('DELETE /api/v1/users/me/consent', (_, _) {
      needsConsent = true;
      return null;
    })
    ..on('PUT /api/v1/chats/{id}/intensity', (_, _) => {'ok': true})
    ..on(
      'GET /api/v1/chats',
      (_, _) => [chatJson(3, done: true, title: 'Old chat')],
    )
    ..on(
      'GET /api/v1/profile',
      (_, _) => {
        'gender': null,
        'age_range': null,
        'nightmare_frequency': 'Rarely',
      },
    )
    ..on(
      'POST /api/v1/chats/start',
      (_, body) => {
        'chat_id': 7,
        'language': body['language'],
        'current_stage': 'recording',
      },
    )
    ..on(
      'POST /api/v1/chats/{id}/message/stream',
      (_, body) => body['content'] == 'yes, done'
          ? streamTurn(
              userText: 'yes, done',
              reply: 'Thank you for this session.',
              stage: 'final',
              done: true,
            )
          : streamTurn(
              userText: body['content'] as String,
              reply: 'Tell me more about it.',
            ),
    );
  final server = ServerConfig(
    store,
    consentVersion: consentTextVersion,
    httpClient: api.httpClient(),
    pageOrigin: pageOrigin,
  );
  if (connected) {
    await server.connect('http://fake');
    api.calls.clear();
  }
  final services = AppServices(
    api: api.client(token: 'test'),
    store: store,
    notifications: NotificationService(),
    voiceRecorder: recorder == null ? null : () => recorder,
  );
  final app = MultiProvider(
    providers: [
      ChangeNotifierProvider<ServerConfig>.value(value: server),
      ChangeNotifierProvider<AuthService>.value(value: auth ?? FakeAuth()),
      ChangeNotifierProvider<AppSettings>.value(value: settings),
      Provider<AppServices>.value(value: services),
    ],
    child: const ReDreamApp(),
  );
  return (app, api, settings);
}

Future<void> _settle(WidgetTester tester) async {
  for (var i = 0; i < 10; i++) {
    await tester.pump(const Duration(milliseconds: 100));
  }
}

/// Phone-sized but tall enough that lazily built lists render every card.
void _tallPhone(WidgetTester tester) {
  tester.view.physicalSize = const Size(1200, 6000);
  tester.view.devicePixelRatio = 3;
  addTearDown(tester.view.reset);
}

void main() {
  testWidgets('signed out shows the sign-in screen and signs in', (
    tester,
  ) async {
    final auth = FakeAuth();
    final (app, _, _) = await _app(auth: auth);
    _tallPhone(tester);
    await tester.pumpWidget(app);
    await _settle(tester);

    expect(find.text('Transform your Dreams'), findsOneWidget);
    await tester.enterText(find.byType(TextFormField).at(0), 'me@example.com');
    await tester.enterText(find.byType(TextFormField).at(1), 'secret1');
    await tester.tap(find.widgetWithText(FilledButton, 'Sign in'));
    await _settle(tester);

    expect(auth.signIns, ['me@example.com']);
    expect(find.text('Your Progress'), findsOneWidget);
  });

  testWidgets('sign-up states the age the app is made for', (tester) async {
    final (app, _, _) = await _app(auth: FakeAuth());
    _tallPhone(tester);
    await tester.pumpWidget(app);
    await _settle(tester);

    await tester.tap(find.text("Don't have an account? Sign up"));
    await _settle(tester);

    expect(
      find.text('By signing up you confirm that you are 18 or older.'),
      findsOneWidget,
    );
  });

  testWidgets('sign-up sends the invite code and goes to onboarding', (
    tester,
  ) async {
    final auth = FakeAuth();
    final (app, _, settings) = await _app(auth: auth);
    _tallPhone(tester);
    await tester.pumpWidget(app);
    await _settle(tester);

    await tester.tap(find.text("Don't have an account? Sign up"));
    await _settle(tester);
    await tester.enterText(find.byType(TextFormField).at(0), 'new@example.com');
    await tester.enterText(find.byType(TextFormField).at(1), 'a-good-password');
    await tester.enterText(find.byType(TextFormField).at(2), 'a-good-password');
    await tester.enterText(
      find.widgetWithText(TextFormField, _en.signUpInviteCode),
      'let-me-in',
    );
    await tester.tap(find.widgetWithText(FilledButton, 'Sign up'));
    await _settle(tester);

    expect(auth.signUps, [(email: 'new@example.com', inviteCode: 'let-me-in')]);
    expect(settings.needsOnboarding('user-new'), isTrue);
    expect(find.text(_en.signUpAgeNote), findsNothing);
  });

  testWidgets('sign-up asks for 8 characters and explains refusals', (
    tester,
  ) async {
    final auth = FakeAuth()
      ..error = const AuthFailure(AuthFailureKind.inviteInvalid);
    final (app, _, _) = await _app(auth: auth);
    _tallPhone(tester);
    await tester.pumpWidget(app);
    await _settle(tester);

    await tester.tap(find.text("Don't have an account? Sign up"));
    await _settle(tester);
    await tester.enterText(find.byType(TextFormField).at(0), 'new@example.com');
    await tester.enterText(find.byType(TextFormField).at(1), 'seven77');
    await tester.enterText(find.byType(TextFormField).at(2), 'seven77');
    await tester.tap(find.widgetWithText(FilledButton, 'Sign up'));
    await _settle(tester);
    expect(find.text(_en.validationPasswordMin(8)), findsOneWidget);

    await tester.enterText(find.byType(TextFormField).at(1), 'a-good-password');
    await tester.enterText(find.byType(TextFormField).at(2), 'a-good-password');
    await tester.enterText(
      find.widgetWithText(TextFormField, _en.signUpInviteCode),
      'not-a-code',
    );
    await tester.tap(find.widgetWithText(FilledButton, 'Sign up'));
    await _settle(tester);
    expect(find.text(_en.authInviteInvalid), findsOneWidget);
    expect(auth.isSignedIn, isFalse);
  });

  testWidgets('sign-up without a code says the server needs one', (
    tester,
  ) async {
    // An invite-only server answers a missing code like a wrong one.
    final auth = FakeAuth()
      ..error = const AuthFailure(AuthFailureKind.inviteInvalid);
    final (app, _, _) = await _app(auth: auth);
    _tallPhone(tester);
    await tester.pumpWidget(app);
    await _settle(tester);

    await tester.tap(find.text("Don't have an account? Sign up"));
    await _settle(tester);
    await tester.enterText(find.byType(TextFormField).at(0), 'new@example.com');
    await tester.enterText(find.byType(TextFormField).at(1), 'a-good-password');
    await tester.enterText(find.byType(TextFormField).at(2), 'a-good-password');
    await tester.enterText(
      find.widgetWithText(TextFormField, _en.signUpInviteCode),
      '  ',
    );
    await tester.tap(find.widgetWithText(FilledButton, 'Sign up'));
    await _settle(tester);

    expect(
      find.text(
        'This server needs an invite code. Please enter the one you were '
        'given.',
      ),
      findsOneWidget,
    );
    expect(find.text(_en.authInviteInvalid), findsNothing);
  });

  testWidgets('sign-in explains wrong credentials', (tester) async {
    final auth = FakeAuth()
      ..error = const AuthFailure(AuthFailureKind.invalidCredentials);
    final (app, _, _) = await _app(auth: auth);
    _tallPhone(tester);
    await tester.pumpWidget(app);
    await _settle(tester);

    await tester.enterText(find.byType(TextFormField).at(0), 'me@example.com');
    await tester.enterText(find.byType(TextFormField).at(1), 'not-my-password');
    await tester.tap(find.widgetWithText(FilledButton, 'Sign in'));
    await _settle(tester);

    expect(find.text(_en.signInError), findsOneWidget);
    expect(auth.isSignedIn, isFalse);
  });

  testWidgets('sign-in shows the wait after too many attempts', (
    tester,
  ) async {
    final auth = FakeAuth()
      ..error = const AuthFailure(
        AuthFailureKind.tooManyAttempts,
        retryAfterSeconds: 540,
      );
    final (app, _, _) = await _app(auth: auth);
    _tallPhone(tester);
    await tester.pumpWidget(app);
    await _settle(tester);

    await tester.enterText(find.byType(TextFormField).at(0), 'me@example.com');
    await tester.enterText(find.byType(TextFormField).at(1), 'secret1');
    await tester.tap(find.widgetWithText(FilledButton, 'Sign in'));
    await _settle(tester);

    expect(
      find.text(
        'Too many attempts. Please try again in 9 minutes.',
      ),
      findsOneWidget,
    );
  });

  group('server address', () {
    testWidgets('a first start asks for the server, then signs in there', (
      tester,
    ) async {
      final (app, api, _) = await _app(auth: FakeAuth(), connected: false);
      _tallPhone(tester);
      await tester.pumpWidget(app);
      await _settle(tester);

      expect(find.text(_en.serverAddressLabel), findsOneWidget);
      expect(find.widgetWithText(FilledButton, 'Sign in'), findsNothing);
      await tester.enterText(find.byType(TextField), 'http://fake/');
      await tester.tap(find.widgetWithText(FilledButton, _en.serverConnect));
      await _settle(tester);

      expect(api.calls, ['GET /api/v1/instance']);
      expect(find.widgetWithText(FilledButton, 'Sign in'), findsOneWidget);
      expect(find.text('Server: http://fake'), findsOneWidget);
    });

    testWidgets('a server on another version is refused with the reason', (
      tester,
    ) async {
      final (app, _, _) = await _app(
        auth: FakeAuth(),
        connected: false,
        instance: instanceJson(apiVersion: 2),
      );
      _tallPhone(tester);
      await tester.pumpWidget(app);
      await _settle(tester);

      await tester.enterText(find.byType(TextField), 'http://fake');
      await tester.tap(find.widgetWithText(FilledButton, _en.serverConnect));
      await _settle(tester);

      expect(find.text(_en.serverAppOutdated), findsOneWidget);
      expect(find.text(_en.serverAddressLabel), findsOneWidget);
    });

    testWidgets('the sign-in screen can go back to the address', (
      tester,
    ) async {
      final (app, _, _) = await _app(auth: FakeAuth());
      _tallPhone(tester);
      await tester.pumpWidget(app);
      await _settle(tester);

      await tester.tap(find.text(_en.serverChange));
      await _settle(tester);

      expect(find.text(_en.serverAddressLabel), findsOneWidget);
      expect(
        tester.widget<TextField>(find.byType(TextField)).controller!.text,
        'http://fake',
      );
    });

    testWidgets('changing the server in settings signs out first', (
      tester,
    ) async {
      final auth = FakeAuth(userId: 'user-1', email: 'me@example.com');
      final (app, _, _) = await _app(auth: auth);
      _tallPhone(tester);
      await tester.pumpWidget(app);
      await _settle(tester);

      final server = tester
          .element(find.byType(MaterialApp))
          .read<ServerConfig>();
      bool? connectedAtSignOut;
      auth.onSignOut = () => connectedAtSignOut = server.isConnected;
      await tester.tap(find.byIcon(Icons.settings_outlined));
      await _settle(tester);
      expect(find.text('http://fake'), findsOneWidget);
      await tester.tap(find.text(_en.settingsServer));
      await _settle(tester);
      expect(find.text(_en.changeServerBody), findsOneWidget);
      await tester.tap(find.widgetWithText(TextButton, _en.serverChange));
      await _settle(tester);

      expect(connectedAtSignOut, isTrue);
      expect(auth.isSignedIn, isFalse);
      expect(find.text(_en.serverAddressLabel), findsOneWidget);
      expect(server.isConnected, isFalse);
    });

    testWidgets('sign-up asks for an invite code only where one is needed', (
      tester,
    ) async {
      final auth = FakeAuth();
      final (app, _, _) = await _app(
        auth: auth,
        instance: instanceJson(registration: 'open'),
      );
      _tallPhone(tester);
      await tester.pumpWidget(app);
      await _settle(tester);

      await tester.tap(find.text("Don't have an account? Sign up"));
      await _settle(tester);
      expect(find.text(_en.signUpInviteCode), findsNothing);
      expect(find.byType(TextFormField), findsNWidgets(3));

      // The server became invite-only since its info was read.
      auth.error = const AuthFailure(AuthFailureKind.inviteInvalid);
      await tester.enterText(find.byType(TextFormField).at(0), 'a@example.com');
      await tester.enterText(find.byType(TextFormField).at(1), 'a-password');
      await tester.enterText(find.byType(TextFormField).at(2), 'a-password');
      await tester.tap(find.widgetWithText(FilledButton, 'Sign up'));
      await _settle(tester);

      expect(find.text(_en.authInviteRequired), findsOneWidget);
      expect(find.text(_en.signUpInviteCode), findsOneWidget);
    });

    testWidgets('a closed server offers no sign-up', (tester) async {
      final (app, _, _) = await _app(
        auth: FakeAuth(),
        instance: instanceJson(registration: 'closed'),
      );
      _tallPhone(tester);
      await tester.pumpWidget(app);
      await _settle(tester);

      expect(find.widgetWithText(FilledButton, 'Sign in'), findsOneWidget);
      expect(find.text("Don't have an account? Sign up"), findsNothing);
    });
  });

  group('what the server offers', () {
    testWidgets('no voice: no mic in the session, no Listen on a dream', (
      tester,
    ) async {
      final (app, _, _) = await _app(
        auth: FakeAuth(userId: 'user-1'),
        recorder: _FakeRecorder(),
        instance: instanceJson(voice: false),
      );
      _tallPhone(tester);
      await tester.pumpWidget(app);
      await _settle(tester);

      await tester.tap(find.text('Session'));
      await _settle(tester);
      expect(find.byKey(const ValueKey('mic')), findsNothing);

      await tester.tap(find.text('Journal'));
      await _settle(tester);
      await tester.tap(find.text('Lost in Dark Forest'));
      await _settle(tester);
      expect(find.text(_en.detailListen), findsNothing);
    });

    testWidgets('speech turned on while the app runs brings the mic', (
      tester,
    ) async {
      final (app, api, _) = await _app(
        auth: FakeAuth(userId: 'user-1'),
        recorder: _FakeRecorder(),
        instance: instanceJson(voice: false),
      );
      _tallPhone(tester);
      await tester.pumpWidget(app);
      await _settle(tester);
      await tester.tap(find.text('Session'));
      await _settle(tester);
      expect(find.byKey(const ValueKey('mic')), findsNothing);

      api.on('GET /api/v1/instance', (_, _) => instanceJson());
      await tester
          .element(find.byType(MaterialApp))
          .read<ServerConfig>()
          .refresh();
      await _settle(tester);

      expect(find.byKey(const ValueKey('mic')), findsOneWidget);
    });

    testWidgets('without speech, audio a dream already has stays', (
      tester,
    ) async {
      final (app, api, _) = await _app(
        auth: FakeAuth(userId: 'user-1'),
        instance: instanceJson(voice: false),
      );
      api.on(
        'GET /api/v1/dreams',
        (_, _) => [
          {
            ...dreamJson(1, current: true),
            'rehearsal_audio_url': '/api/v1/audio/files/en-1.mp3?exp=1&sig=a',
            'rehearsal_language': 'en',
          },
        ],
      );
      _tallPhone(tester);
      await tester.pumpWidget(app);
      await _settle(tester);

      await tester.tap(find.text('Journal'));
      await _settle(tester);
      await tester.tap(find.text('Lost in Dark Forest'));
      await _settle(tester);
      expect(find.text(_en.detailListen), findsOneWidget);
    });

    testWidgets('with voice the dream offers Listen', (tester) async {
      final (app, _, _) = await _app(auth: FakeAuth(userId: 'user-1'));
      _tallPhone(tester);
      await tester.pumpWidget(app);
      await _settle(tester);

      await tester.tap(find.text('Journal'));
      await _settle(tester);
      await tester.tap(find.text('Lost in Dark Forest'));
      await _settle(tester);
      expect(find.text(_en.detailListen), findsOneWidget);
    });

    testWidgets('settings name no contact or policy the server lacks', (
      tester,
    ) async {
      final (app, _, _) = await _app(auth: FakeAuth(userId: 'user-1'));
      _tallPhone(tester);
      await tester.pumpWidget(app);
      await _settle(tester);

      await tester.tap(find.byIcon(Icons.settings_outlined));
      await _settle(tester);

      expect(find.text(_en.settingsSupportSection), findsNothing);
      expect(find.text(_en.settingsContact), findsNothing);
      expect(find.text(_en.settingsPrivacyPolicy), findsNothing);
      expect(find.textContaining('redreamai.com'), findsNothing);
    });

    testWidgets("settings lead to the operator's contact and policy", (
      tester,
    ) async {
      final (app, _, _) = await _app(
        auth: FakeAuth(userId: 'user-1'),
        instance: instanceJson(
          operatorName: 'Dream Club',
          contact: 'help@dreams.example.org',
          privacyUrl: 'https://dreams.example.org/privacy',
        ),
      );
      _tallPhone(tester);
      await tester.pumpWidget(app);
      await _settle(tester);

      await tester.tap(find.byIcon(Icons.settings_outlined));
      await _settle(tester);
      expect(find.text(_en.settingsPrivacyPolicy), findsOneWidget);
      expect(find.text('help@dreams.example.org'), findsOneWidget);
      await tester.tap(find.text(_en.settingsContact));
      await _settle(tester);

      expect(find.text('Run by Dream Club'), findsOneWidget);
      expect(find.text(_en.contactOpenEmail), findsOneWidget);
      expect(find.text(_en.contactCopy), findsOneWidget);
    });

    testWidgets('forgot password shows whom to ask', (tester) async {
      final (app, _, _) = await _app(
        auth: FakeAuth(),
        instance: instanceJson(contact: 'https://dreams.example.org/help'),
      );
      _tallPhone(tester);
      await tester.pumpWidget(app);
      await _settle(tester);

      await tester.tap(find.text('Forgot password?'));
      await _settle(tester);

      expect(find.text('https://dreams.example.org/help'), findsOneWidget);
    });
  });

  testWidgets('forgot password says who can reset it', (tester) async {
    final (app, api, _) = await _app(auth: FakeAuth());
    _tallPhone(tester);
    await tester.pumpWidget(app);
    await _settle(tester);

    await tester.tap(find.text('Forgot password?'));
    await _settle(tester);

    expect(find.text(_en.forgotPasswordBody), findsOneWidget);
    expect(find.byType(TextFormField), findsNothing);
    await tester.tap(find.text('Back to sign in'));
    await _settle(tester);
    expect(find.text('Forgot password?'), findsOneWidget);
    expect(api.calls, isEmpty);
  });

  group('a personal server (make start)', () {
    testWidgets('signs in by itself, no sign-in screen', (tester) async {
      final auth = FakeAuth();
      final (app, _, _) = await _app(
        auth: auth,
        instance: instanceJson(registration: 'personal'),
      );
      _tallPhone(tester);
      await tester.pumpWidget(app);
      await _settle(tester);

      expect(auth.personalSignIns, 1);
      expect(auth.signIns, isEmpty);
      // A new local user starts like a sign-up: with onboarding.
      expect(find.text(_en.skip), findsOneWidget);
    });

    testWidgets('the same local user again goes straight home', (
      tester,
    ) async {
      final auth = FakeAuth();
      final (app, _, _) = await _app(
        auth: auth,
        instance: instanceJson(registration: 'personal'),
      );
      _tallPhone(tester);
      await tester.pumpWidget(app);
      await _settle(tester);
      await tester.tap(find.text(_en.skip));
      await _settle(tester);
      expect(find.text('Your Progress'), findsOneWidget);

      // The session ends (an expired token): the app signs in again by itself.
      await auth.signOut();
      await _settle(tester);

      expect(auth.personalSignIns, 2);
      expect(find.text(_en.skip), findsNothing);
      expect(find.text('Your Progress'), findsOneWidget);
    });

    testWidgets('says where it opens when it refuses, and tries again', (
      tester,
    ) async {
      final auth = FakeAuth()..error = Exception('403');
      final (app, _, _) = await _app(
        auth: auth,
        instance: instanceJson(registration: 'personal'),
      );
      _tallPhone(tester);
      await tester.pumpWidget(app);
      await _settle(tester);

      expect(find.text(_en.personalSignInFailed), findsOneWidget);
      expect(find.byType(TextFormField), findsNothing);
      auth.error = null;
      await tester.tap(find.widgetWithText(FilledButton, _en.retry));
      await _settle(tester);

      expect(find.text(_en.skip), findsOneWidget);
    });

    testWidgets('without the key it points to the link from the terminal', (
      tester,
    ) async {
      final auth = FakeAuth()
        ..error = const AuthFailure(AuthFailureKind.personalKey);
      final (app, _, _) = await _app(
        auth: auth,
        instance: instanceJson(registration: 'personal'),
      );
      _tallPhone(tester);
      await tester.pumpWidget(app);
      await _settle(tester);

      expect(find.text(_en.personalKeyNeeded), findsOneWidget);
      expect(find.widgetWithText(FilledButton, _en.retry), findsOneWidget);
    });

    testWidgets('settings have no account to change or sign out of', (
      tester,
    ) async {
      final (app, _, _) = await _app(
        auth: FakeAuth(userId: 'user-personal', email: 'personal@localhost'),
        instance: instanceJson(registration: 'personal'),
      );
      _tallPhone(tester);
      await tester.pumpWidget(app);
      await _settle(tester);

      await tester.tap(find.byIcon(Icons.settings_outlined));
      await _settle(tester);

      expect(find.text(_en.settingsEmail), findsNothing);
      expect(find.text(_en.settingsPassword), findsNothing);
      expect(find.text(_en.settingsSignOut), findsNothing);
      expect(find.text(_en.settingsNightmareFrequency), findsOneWidget);
      expect(find.text(_en.settingsExport), findsOneWidget);
      expect(find.text(_en.settingsDeleteAccount), findsOneWidget);
    });

    testWidgets('consent offers no sign-out', (tester) async {
      final (app, _, _) = await _app(
        auth: FakeAuth(userId: 'user-personal'),
        consentRequired: true,
        instance: instanceJson(registration: 'personal'),
      );
      _tallPhone(tester);
      await tester.pumpWidget(app);
      await _settle(tester);

      expect(find.text(_en.consentTitle), findsOneWidget);
      expect(find.text(_en.consentDeclineHintPersonal), findsOneWidget);
      expect(find.text(_en.consentSignOut), findsNothing);
    });
  });

  group('the web app', () {
    testWidgets('connects to the server it came from, with no change offered', (
      tester,
    ) async {
      final (app, api, _) = await _app(
        auth: FakeAuth(),
        connected: false,
        pageOrigin: 'http://fake',
      );
      _tallPhone(tester);
      await tester.pumpWidget(app);
      await _settle(tester);

      expect(api.calls, contains('GET /api/v1/instance'));
      expect(find.widgetWithText(FilledButton, 'Sign in'), findsOneWidget);
      expect(find.text(_en.serverChange), findsNothing);
    });

    testWidgets('settings show the server but offer no change or reminder', (
      tester,
    ) async {
      PlatformFeatures.debugIsWeb = true;
      addTearDown(() => PlatformFeatures.debugIsWeb = null);
      final (app, _, _) = await _app(
        auth: FakeAuth(userId: 'user-1', email: 'me@example.com'),
        pageOrigin: 'http://fake',
      );
      _tallPhone(tester);
      await tester.pumpWidget(app);
      await _settle(tester);

      await tester.tap(find.byIcon(Icons.settings_outlined));
      await _settle(tester);
      expect(find.text('http://fake'), findsOneWidget);
      await tester.tap(find.text(_en.settingsServer));
      await _settle(tester);

      expect(find.text(_en.changeServerBody), findsNothing);
      expect(find.text(_en.reminderTitle), findsNothing);
      expect(find.text(_en.settingsSignOut), findsOneWidget);
    });

    testWidgets('the journal offers no reminder on the web', (tester) async {
      PlatformFeatures.debugIsWeb = true;
      addTearDown(() => PlatformFeatures.debugIsWeb = null);
      final (app, _, _) = await _app(
        auth: FakeAuth(userId: 'user-1', email: 'me@example.com'),
        pageOrigin: 'http://fake',
      );
      _tallPhone(tester);
      await tester.pumpWidget(app);
      await _settle(tester);

      await tester.tap(find.text(_en.navJournal));
      await _settle(tester);

      expect(find.text(_en.journalAddDream), findsOneWidget);
      expect(find.text(_en.journalNotifications), findsNothing);
    });
  });

  testWidgets('home shows progress from the API and tabs switch screens', (
    tester,
  ) async {
    final (app, api, _) = await _app(
      auth: FakeAuth(userId: 'user-1', email: 'me@example.com'),
    );
    _tallPhone(tester);
    await tester.pumpWidget(app);
    await _settle(tester);

    expect(find.text('Begin Session'), findsOneWidget);
    expect(find.text('2 days'), findsOneWidget);
    for (final shortcut in [
      'Daily rehearsal',
      'Rewriting Guide',
      'Learn more about sleep and dreams',
    ]) {
      expect(find.text(shortcut), findsOneWidget);
    }
    // Each shortcut carries its picture; rehearsal and guide side by side,
    // Learn across the page below them.
    for (final picture in ['rehearsal', 'guide', 'learn']) {
      expect(
        find.image(AssetImage('assets/images/home_$picture.webp')),
        findsOneWidget,
      );
    }
    expect(
      tester.getCenter(find.text(_en.homeLearnTitle)).dy,
      greaterThan(tester.getCenter(find.text('Rewriting Guide')).dy),
    );
    expect(
      tester.getCenter(find.text('Daily rehearsal')).dy,
      tester.getCenter(find.text('Rewriting Guide')).dy,
    );
    expect(
      api.calls,
      containsAll(['GET /api/v1/dreams', 'GET /api/v1/progress/stats']),
    );

    await tester.tap(find.text('Journal'));
    await _settle(tester);
    expect(find.text('Dream Journal'), findsOneWidget);
    expect(find.text('Lost in Dark Forest'), findsOneWidget);
    expect(find.text('Intensity: 8/10'), findsOneWidget);
    expect(find.text('1 / 14 rehearsed'), findsOneWidget);
    // Not practiced today: no daily mark, and a single progress display.
    expect(find.text(_en.journalDailyDone), findsNothing);
    expect(find.byType(LinearProgressIndicator), findsNothing);
    expect(find.text('Rehearse Dream'), findsOneWidget);
    expect(find.text('Flying'), findsOneWidget);

    await tester.tap(find.text('Nightmares'));
    await _settle(tester);
    expect(find.text('Flying'), findsNothing);
    expect(find.text('Lost in Dark Forest'), findsOneWidget);
  });

  testWidgets('the top bar shows the cloud except where the chat menu sits', (
    tester,
  ) async {
    final (app, _, settings) = await _app(auth: FakeAuth(userId: 'user-1'));
    await settings.setLanguage('de');
    _tallPhone(tester);
    await tester.pumpWidget(app);
    await _settle(tester);

    expect(find.byType(BrandMark), findsOneWidget);
    expect(find.text('reDreamOSS', findRichText: true), findsOneWidget);
    // The slogan stays English in the German app.
    expect(find.text('Transform your Dreams'), findsOneWidget);

    await tester.tap(find.text('Sitzung'));
    await _settle(tester);
    expect(find.byType(BrandMark), findsNothing);
    expect(find.byIcon(Icons.menu), findsOneWidget);
  });

  testWidgets('an untouched chat follows a change of the app language', (
    tester,
  ) async {
    final (app, api, settings) = await _app(auth: FakeAuth(userId: 'user-1'));
    api.on('DELETE /api/v1/chats/{id}', (_, _) => null);
    _tallPhone(tester);
    await tester.pumpWidget(app);
    await _settle(tester);
    await tester.tap(find.text('Session'));
    await _settle(tester);
    expect(find.text(_en.chatWelcome2), findsOneWidget);

    await settings.setLanguage('de');
    await _settle(tester);

    expect(api.calls, contains('DELETE /api/v1/chats/7'));
    expect(
      api.calls.where((c) => c == 'POST /api/v1/chats/start'),
      hasLength(2),
    );
    expect(find.text(AppLocalizationsDe().chatWelcome2), findsOneWidget);
  });

  testWidgets('session tab sends a message and renders the reply', (
    tester,
  ) async {
    final (app, api, _) = await _app(
      auth: FakeAuth(userId: 'user-1', email: 'me@example.com'),
    );
    _tallPhone(tester);
    await tester.pumpWidget(app);
    await _settle(tester);

    await tester.tap(find.text('Session'));
    await _settle(tester);
    expect(find.text('Dream Companion'), findsOneWidget);
    // Chat 7 gets the second of the three greetings and input hints.
    expect(find.text(_en.chatWelcome2), findsOneWidget);
    expect(find.text('Start redreaming…'), findsOneWidget);
    expect(AppLocalizationsDe().chatInputHint2, 'Start redreaming…');

    await tester.enterText(
      find.byType(TextField).last,
      'I dreamt about losing my dog',
    );
    await _settle(tester);
    await tester.tap(find.byIcon(Icons.send_rounded));
    await _settle(tester);

    expect(api.calls, contains('POST /api/v1/chats/start'));
    expect(api.calls, contains('POST /api/v1/chats/7/message/stream'));
    expect(find.text('I dreamt about losing my dog'), findsOneWidget);
    expect(find.text('Tell me more about it.'), findsOneWidget);

    await tester.tap(find.byIcon(Icons.menu));
    await _settle(tester);
    expect(find.text('Chat History'), findsOneWidget);
    expect(find.text('Old chat'), findsOneWidget);
  });

  group('voice note', () {
    const transcribe = 'POST /api/v1/audio/transcribe';
    const post = 'POST /api/v1/chats/7/message/stream';
    final mic = find.byKey(const ValueKey('mic'));

    Future<(FakeApi, _FakeRecorder)> openSession(WidgetTester tester) async {
      final recorder = _FakeRecorder();
      final (app, api, _) = await _app(
        auth: FakeAuth(userId: 'user-1'),
        recorder: recorder,
      );
      api.on(transcribe, (_, _) => {'text': 'I was lost in a forest'});
      _tallPhone(tester);
      await tester.pumpWidget(app);
      await _settle(tester);
      await tester.tap(find.text('Session'));
      await _settle(tester);
      return (api, recorder);
    }

    testWidgets('a tap records hands-free and Send posts the words', (
      tester,
    ) async {
      final (api, _) = await openSession(tester);
      final micAt = tester.getCenter(mic);
      final sendAt = tester.getCenter(find.byTooltip(_en.chatSend));

      await tester.tap(mic);
      await _settle(tester);
      expect(find.text(_en.chatMicRecording), findsOneWidget);
      expect(api.calls, isNot(contains(transcribe)));
      // Discard takes the mic's place and Send stays where sending is.
      expect(tester.getCenter(find.byTooltip(_en.chatMicCancel)), micAt);
      expect(tester.getCenter(find.byTooltip(_en.chatMicStop)), sendAt);

      await tester.tap(find.byTooltip(_en.chatMicStop));
      await _settle(tester);
      expect(api.calls, containsAll([transcribe, post]));
      expect(find.text('I was lost in a forest'), findsOneWidget);
      expect(mic, findsOneWidget);
    });

    testWidgets('Discard drops a hands-free recording', (tester) async {
      final (api, recorder) = await openSession(tester);

      await tester.tap(mic);
      await _settle(tester);
      await tester.tap(find.byTooltip(_en.chatMicCancel));
      await _settle(tester);

      expect(recorder.cancelled, isTrue);
      expect(api.calls, isNot(contains(transcribe)));
      expect(find.text(_en.chatMicRecording), findsNothing);
    });

    testWidgets('holding records and letting go sends', (tester) async {
      final (api, _) = await openSession(tester);

      final finger = await tester.createGesture();
      await finger.down(tester.getCenter(mic));
      await _settle(tester);
      expect(find.text(_en.chatMicSlideDiscard), findsOneWidget);
      expect(api.calls, isNot(contains(transcribe)));

      await finger.up(timeStamp: const Duration(seconds: 3));
      await _settle(tester);
      expect(api.calls, containsAll([transcribe, post]));
      expect(find.text('I was lost in a forest'), findsOneWidget);
    });

    testWidgets('sliding left while holding discards', (tester) async {
      final (api, recorder) = await openSession(tester);

      final finger = await tester.createGesture();
      await finger.down(tester.getCenter(mic));
      await _settle(tester);
      await finger.moveBy(const Offset(-100, 0));
      await _settle(tester);
      await finger.up(timeStamp: const Duration(seconds: 3));
      await _settle(tester);

      expect(recorder.cancelled, isTrue);
      expect(api.calls, isNot(contains(transcribe)));
      expect(find.text(_en.chatMicRecording), findsNothing);
    });

    testWidgets('sliding up while holding keeps recording hands-free', (
      tester,
    ) async {
      final (api, recorder) = await openSession(tester);

      final finger = await tester.createGesture();
      await finger.down(tester.getCenter(mic));
      await _settle(tester);
      await finger.moveBy(const Offset(0, -100));
      await finger.up(timeStamp: const Duration(seconds: 3));
      await _settle(tester);

      // Letting go after the lock sends nothing; the buttons decide.
      expect(find.byTooltip(_en.chatMicStop), findsOneWidget);
      expect(api.calls, isNot(contains(transcribe)));
      expect(recorder.cancelled, isFalse);

      await tester.tap(find.byTooltip(_en.chatMicCancel));
      await _settle(tester);
      expect(recorder.cancelled, isTrue);
    });
  });

  testWidgets('back closes the chat history instead of leaving the app', (
    tester,
  ) async {
    final (app, _, _) = await _app(
      auth: FakeAuth(userId: 'user-1', email: 'me@example.com'),
    );
    _tallPhone(tester);
    await tester.pumpWidget(app);
    await _settle(tester);

    await tester.tap(find.text('Session'));
    await _settle(tester);
    await tester.tap(find.byIcon(Icons.menu));
    await _settle(tester);
    expect(find.text('Chat History'), findsOneWidget);

    // What Android's back button or gesture sends.
    await tester.binding.handlePopRoute();
    await _settle(tester);
    expect(find.text('Chat History'), findsNothing);
    expect(find.text('Dream Companion'), findsOneWidget);
  });

  testWidgets('each mood has its colour, and the chosen one is framed', (
    tester,
  ) async {
    final (app, api, _) = await _app(auth: FakeAuth(userId: 'user-1'));
    String? saved;
    api
      ..on('POST /api/v1/progress/emotion', (_, body) {
        saved = body['emotion'] as String;
        return null;
      })
      ..on('GET /api/v1/progress/stats', (_, _) => statsJson(mood: saved));
    _tallPhone(tester);
    await tester.pumpWidget(app);
    await _settle(tester);

    BoxDecoration tile(String mood) =>
        tester
                .widget<AnimatedContainer>(
                  find.descendant(
                    of: find.byTooltip(mood),
                    matching: find.byType(AnimatedContainer),
                  ),
                )
                .decoration!
            as BoxDecoration;

    // Lilac, blue, turquoise, green, yellow, like the session progress.
    const moods = {
      'Very dissatisfied': BrandColors.lilac,
      'Dissatisfied': BrandColors.periwinkle,
      'Neutral': BrandColors.turquoise,
      'Satisfied': BrandColors.green,
      'Very satisfied': BrandColors.yellow,
    };
    expect(stageColors, [
      BrandColors.lilac,
      BrandColors.turquoise,
      BrandColors.green,
      BrandColors.yellow,
    ]);
    final washes = {for (final mood in moods.keys) tile(mood).color};
    expect(washes, hasLength(5));
    for (final mood in moods.keys) {
      expect(tile(mood).border!.top.color, Colors.transparent);
    }

    await tester.tap(find.byTooltip('Satisfied'));
    await _settle(tester);
    for (final MapEntry(key: mood, value: color) in moods.entries) {
      expect(
        tile(mood).border!.top.color,
        mood == 'Satisfied' ? color : Colors.transparent,
      );
    }
  });

  testWidgets('back leads from every other tab to Home', (tester) async {
    final (app, _, _) = await _app(
      auth: FakeAuth(userId: 'user-1', email: 'me@example.com'),
    );
    _tallPhone(tester);
    await tester.pumpWidget(app);
    await _settle(tester);
    final bar = find.byType(NavigationBar);
    int selected() => tester.widget<NavigationBar>(bar).selectedIndex;

    for (final (label, index) in [
      ('Session', 1),
      ('Journal', 2),
      ('Learning', 3),
    ]) {
      await tester.tap(find.descendant(of: bar, matching: find.text(label)));
      await _settle(tester);
      expect(selected(), index);

      // What Android's back button or gesture sends.
      expect(await tester.binding.handlePopRoute(), isTrue);
      await _settle(tester);
      expect(selected(), 0, reason: 'back from $label');
    }

    // With the chat history open, the first Back only closes it.
    await tester.tap(find.descendant(of: bar, matching: find.text('Session')));
    await _settle(tester);
    await tester.tap(find.byIcon(Icons.menu));
    await _settle(tester);
    await tester.binding.handlePopRoute();
    await _settle(tester);
    expect(find.text('Chat History'), findsNothing);
    expect(selected(), 1);
    await tester.binding.handlePopRoute();
    await _settle(tester);
    expect(selected(), 0);

    // On Home there is nothing left to go back to: Back leaves the app.
    final shell = tester.widget<PopScope<Object?>>(
      find.byWidgetPredicate((w) => w is PopScope<Object?>).first,
    );
    expect(shell.canPop, isTrue);
  });

  testWidgets('journal buttons show their full labels', (tester) async {
    final (app, _, _) = await _app(
      auth: FakeAuth(userId: 'user-1', email: 'me@example.com'),
    );
    _tallPhone(tester);
    await tester.pumpWidget(app);
    await _settle(tester);

    await tester.tap(find.text('Journal'));
    await _settle(tester);

    for (final label in [_en.journalAddDream, _en.journalNotifications]) {
      final text = tester.renderObject<RenderParagraph>(find.text(label));
      // A label cut off with "…" is laid out narrower than its text needs.
      expect(
        text.size.width,
        moreOrLessEquals(
          text.getMaxIntrinsicWidth(double.infinity),
          epsilon: 0.5,
        ),
        reason: label,
      );
    }
  });

  testWidgets('theme toggle persists the chosen mode', (tester) async {
    final (app, _, settings) = await _app(auth: FakeAuth(userId: 'user-1'));
    _tallPhone(tester);
    await tester.pumpWidget(app);
    await _settle(tester);

    Color? sunColor() =>
        tester.widget<Icon>(find.byIcon(Icons.light_mode_outlined)).color;
    expect(sunColor(), BrandColors.sun);

    // A tap anywhere on the pill switches mode, also on the active symbol.
    await tester.tap(find.byIcon(Icons.light_mode_outlined));
    await _settle(tester);
    expect(settings.themeMode, ThemeMode.dark);
    expect(
      Theme.of(tester.element(find.text('Your Progress'))).brightness,
      Brightness.dark,
    );
    // The sun is only yellow while light mode is the active one.
    expect(sunColor(), isNot(BrandColors.sun));

    await tester.tap(find.byType(ThemeModeToggle));
    await _settle(tester);
    expect(settings.themeMode, ThemeMode.light);
  });

  testWidgets('the guide shows three steps and leads into a session', (
    tester,
  ) async {
    final (app, _, _) = await _app(
      auth: FakeAuth(userId: 'user-1', email: 'me@example.com'),
    );
    _tallPhone(tester);
    await tester.pumpWidget(app);
    await _settle(tester);

    await tester.tap(find.text('Rewriting Guide'));
    await _settle(tester);
    for (final step in [
      '1. Record your nightmare',
      '2. Rewrite your dream',
      '3. Rehearse your new dream',
    ]) {
      expect(find.text(step), findsOneWidget);
    }

    await tester.tap(find.widgetWithText(FilledButton, 'Begin Session').last);
    await _settle(tester);
    expect(find.text('Dream Companion'), findsOneWidget);
  });

  testWidgets('onboarding ends without any profile answer', (tester) async {
    final (app, api, settings) = await _app(
      auth: FakeAuth(userId: 'user-1', email: 'me@example.com'),
    );
    await settings.markOnboardingPending('user-1');
    _tallPhone(tester);
    await tester.pumpWidget(app);
    await _settle(tester);

    // The title reads on into the logo, which carries the app's name.
    expect(
      tester.getTopLeft(find.text('Welcome to')).dy,
      lessThan(tester.getTopLeft(find.byType(BrandLogo)).dy),
    );
    // No way back from the first page, no way on from the last.
    expect(_arrowShown(tester, Icons.arrow_back), isFalse);
    for (var page = 1; page < 5; page++) {
      await tester.tap(find.byIcon(Icons.arrow_forward));
      await _settle(tester);
      if (page == 2) {
        expect(
          find.textContaining(
            'each session:\n1) Record your nightmare\n2) Rewrite it',
          ),
          findsOneWidget,
        );
      }
    }
    expect(find.text('All answers are optional.'), findsOneWidget);
    expect(_arrowShown(tester, Icons.arrow_forward), isFalse);
    expect(_arrowShown(tester, Icons.arrow_back), isTrue);

    await tester.tap(find.widgetWithText(FilledButton, 'Get started'));
    await _settle(tester);

    expect(api.calls, isNot(contains('POST /api/v1/profile')));
    expect(find.text('Your Progress'), findsOneWidget);
  });

  testWidgets('onboarding saves the answers that were given', (tester) async {
    final (app, api, settings) = await _app(
      auth: FakeAuth(userId: 'user-1', email: 'me@example.com'),
    );
    await settings.markOnboardingPending('user-1');
    Map<String, dynamic>? saved;
    api.on('POST /api/v1/profile', (_, body) => saved = body);
    _tallPhone(tester);
    await tester.pumpWidget(app);
    await _settle(tester);
    for (var page = 1; page < 5; page++) {
      await tester.tap(find.byIcon(Icons.arrow_forward));
      await _settle(tester);
    }

    await tester.tap(find.text('Age range'));
    await _settle(tester);
    // The app is not offered to minors as an age group.
    expect(find.text('Under 18'), findsNothing);
    expect(Profile.ageRanges.first, '18-24');
    await tester.tap(find.text('25-34').last);
    await _settle(tester);
    await tester.tap(find.widgetWithText(FilledButton, 'Get started'));
    await _settle(tester);

    expect(saved, {
      'gender': null,
      'age_range': '25-34',
      'nightmare_frequency': null,
    });
    expect(find.text('Your Progress'), findsOneWidget);
  });

  testWidgets('lists stop at their ends without stretching the screen', (
    tester,
  ) async {
    final (app, _, _) = await _app(auth: FakeAuth(userId: 'user-1'));
    _tallPhone(tester);
    await tester.pumpWidget(app);
    await _settle(tester);

    // Widget tests run as Android, where Material 3 stretches by default.
    expect(find.byType(Scrollable), findsWidgets);
    expect(find.byType(StretchingOverscrollIndicator), findsNothing);
  });

  testWidgets('consent gate blocks until the user agrees', (tester) async {
    final (app, api, _) = await _app(
      auth: FakeAuth(userId: 'user-1'),
      consentRequired: true,
    );
    _tallPhone(tester);
    await tester.pumpWidget(app);
    await _settle(tester);

    expect(find.text(_en.consentTitle), findsOneWidget);
    expect(find.text('Your Progress'), findsNothing);
    final agree = find.widgetWithText(FilledButton, _en.consentContinue);
    expect(tester.widget<FilledButton>(agree).onPressed, isNull);

    await tester.tap(find.byType(Checkbox));
    await _settle(tester);
    await tester.tap(agree);
    await _settle(tester);

    expect(api.calls, contains('POST /api/v1/users/me/consent'));
    expect(find.text('Your Progress'), findsOneWidget);
  });

  testWidgets('settings opens with user state and can withdraw consent', (
    tester,
  ) async {
    final (app, api, _) = await _app(auth: FakeAuth(userId: 'user-1'));
    _tallPhone(tester);
    await tester.pumpWidget(app);
    await _settle(tester);

    await tester.tap(find.byIcon(Icons.settings_outlined));
    await _settle(tester);
    expect(tester.takeException(), isNull);
    expect(api.calls, contains('GET /api/v1/profile'));

    await tester.tap(find.text(_en.settingsWithdrawConsent));
    await _settle(tester);
    await tester.tap(find.text(_en.withdrawConsentConfirm));
    await _settle(tester);

    expect(api.calls, contains('DELETE /api/v1/users/me/consent'));
    expect(find.text(_en.consentTitle), findsOneWidget);
  });

  testWidgets('the nightmare frequency row opens its editor and saves', (
    tester,
  ) async {
    final (app, api, _) = await _app(auth: FakeAuth(userId: 'user-1'));
    api.on(
      'PATCH /api/v1/profile/nightmare-frequency',
      (_, body) => {
        'gender': null,
        'age_range': null,
        'nightmare_frequency': body['nightmare_frequency'],
      },
    );
    _tallPhone(tester);
    await tester.pumpWidget(app);
    await _settle(tester);
    await tester.tap(find.byIcon(Icons.settings_outlined));
    await _settle(tester);
    expect(find.text('Rarely'), findsOneWidget);

    await tester.tap(find.text(_en.settingsNightmareFrequency));
    await _settle(tester);
    await tester.tap(find.text('A few times a week'));
    await tester.tap(find.text(_en.save));
    await _settle(tester);

    expect(api.calls, contains('PATCH /api/v1/profile/nightmare-frequency'));
    expect(find.text('A few times a week'), findsOneWidget);
  });

  testWidgets('Learning is the last tab and its back arrow returns', (
    tester,
  ) async {
    final (app, _, _) = await _app(auth: FakeAuth(userId: 'user-1'));
    _tallPhone(tester);
    await tester.pumpWidget(app);
    await _settle(tester);

    // The tab sits right of Journal.
    final bar = find.byType(NavigationBar);
    Finder tab(String label) =>
        find.descendant(of: bar, matching: find.text(label));
    expect(
      tester.getCenter(tab('Learning')).dx,
      greaterThan(tester.getCenter(tab('Journal')).dx),
    );

    await tester.tap(tab('Journal'));
    await _settle(tester);
    await tester.tap(tab('Learning'));
    await _settle(tester);

    // Learn keeps its own bar with the back arrow, above the bottom menu.
    expect(find.byType(BackButton), findsOneWidget);
    expect(find.byType(BrandWordmark), findsNothing);
    expect(bar, findsOneWidget);

    // The back arrow leads to Home, also when the user came from Journal.
    await tester.tap(find.byType(BackButton));
    await _settle(tester);
    expect(tester.widget<NavigationBar>(bar).selectedIndex, 0);
    expect(find.byType(BackButton), findsNothing);
    expect(find.byType(BrandWordmark), findsOneWidget);

    // Home's Learn card opens the same tab.
    await tester.tap(find.text(_en.homeLearnTitle));
    await _settle(tester);
    expect(find.byType(BackButton), findsOneWidget);
    expect(tester.widget<NavigationBar>(bar).selectedIndex, 3);
    await tester.tap(find.byType(BackButton));
    await _settle(tester);
    expect(tester.widget<NavigationBar>(bar).selectedIndex, 0);
  });

  testWidgets('dream details open from the journal', (tester) async {
    final (app, _, _) = await _app(auth: FakeAuth(userId: 'user-1'));
    _tallPhone(tester);
    await tester.pumpWidget(app);
    await _settle(tester);

    await tester.tap(find.text('Journal'));
    await _settle(tester);
    await tester.tap(find.text('Flying'));
    await _settle(tester);
    expect(tester.takeException(), isNull);
    expect(find.text('Flying'), findsWidgets);
  });

  testWidgets('a manual dream is created in the app language', (
    tester,
  ) async {
    final (app, api, settings) = await _app(auth: FakeAuth(userId: 'user-1'));
    await settings.setLanguage('de');
    Map<String, dynamic>? created;
    api.on('POST /api/v1/dreams', (_, body) {
      created = body;
      return dreamJson(9, title: body['title'] as String, language: 'de');
    });
    _tallPhone(tester);
    await tester.pumpWidget(app);
    await _settle(tester);

    final de = AppLocalizationsDe();
    await tester.tap(find.text(de.navJournal).last);
    await _settle(tester);
    await tester.tap(find.text(de.journalAddDream));
    await _settle(tester);
    await tester.enterText(find.byType(TextField).at(0), 'Fallen');
    await tester.enterText(find.byType(TextField).at(1), 'Ich bin gefallen.');
    await tester.tap(find.text(de.editorSave));
    await _settle(tester);

    expect(created?['language'], 'de');
    expect(created?['source'], 'manual');
  });

  testWidgets("the journal card shows the practices and today's rehearsal", (
    tester,
  ) async {
    final (app, api, _) = await _app(auth: FakeAuth(userId: 'user-1'));
    final today = DateTime.now().toUtc().toIso8601String();
    api.on(
      'GET /api/v1/dreams',
      (_, _) => [
        dreamJson(1, practices: ['2025-11-03T10:00:00Z', today]),
        dreamJson(
          2,
          title: 'All done',
          practices: [
            for (var day = 1; day <= 13; day++)
              '2025-10-${day.toString().padLeft(2, '0')}T10:00:00Z',
            today,
          ],
        ),
      ],
    );
    _tallPhone(tester);
    await tester.pumpWidget(app);
    await _settle(tester);
    await tester.tap(find.text('Journal'));
    await _settle(tester);

    Color? colorOf(String text) =>
        tester.widget<Text>(find.text(text)).style?.color;
    expect(colorOf('2 / 14 rehearsed'), isNot(BrandColors.green));
    expect(colorOf('14 / 14 rehearsed!'), BrandColors.green);
    expect(find.text(_en.journalDailyDone), findsNWidgets(2));
    expect(find.byIcon(Icons.check_circle_rounded), findsNWidgets(2));

    // The dots run from edge to edge; today's is the green one.
    final card = find
        .ancestor(
          of: find.text('2 / 14 rehearsed'),
          matching: find.byType(Column),
        )
        .first;
    final dots = tester
        .widgetList<Container>(
          find.descendant(of: card, matching: find.byType(Container)),
        )
        .where(
          (c) => (c.decoration as BoxDecoration?)?.shape == BoxShape.circle,
        )
        .toList();
    expect(dots, hasLength(14));
    Color? dot(int i) => (dots[i].decoration! as BoxDecoration).color;
    expect(dot(0), BrandColors.turquoise);
    expect(dot(1), BrandColors.green);
    expect(dot(2), isNot(BrandColors.turquoise));
    final row = find
        .ancestor(
          of: find.byWidget(dots.first),
          matching: find.byType(Row),
        )
        .first;
    final text = find.text('2 / 14 rehearsed');
    expect(
      tester.getTopLeft(find.byWidget(dots.first)).dx,
      closeTo(tester.getTopLeft(text).dx, 0.01),
    );
    expect(
      tester.getTopRight(find.byWidget(dots.last)).dx,
      closeTo(tester.getTopRight(row).dx, 0.01),
    );
  });

  testWidgets('staying on the rewritten dream records a practice', (
    tester,
  ) async {
    final (app, api, _) = await _app(auth: FakeAuth(userId: 'user-1'));
    const practice = 'POST /api/v1/dreams/1/practice';
    api.on(
      practice,
      (_, _) => dreamJson(
        1,
        current: true,
        practices: [
          '2025-11-03T10:00:00Z',
          DateTime.now().toUtc().toIso8601String(),
        ],
      ),
    );
    _tallPhone(tester);
    await tester.pumpWidget(app);
    await _settle(tester);
    await tester.tap(find.text('Journal'));
    await _settle(tester);

    // The practice button opens the dream itself, with audio one tap away.
    await tester.tap(find.text('Rehearse Dream'));
    await _settle(tester);
    expect(find.text('I found the way home.'), findsOneWidget);
    expect(find.widgetWithText(FilledButton, _en.detailListen), findsOneWidget);
    expect(find.text(_en.detailRehearsalHint), findsOneWidget);
    expect(find.text(_en.detailMeditation2), findsOneWidget);
    // Chat history sits left of the audio button.
    expect(
      tester.getCenter(find.text(_en.detailChatHistory)).dx,
      lessThan(tester.getCenter(find.text(_en.detailListen)).dx),
    );

    // Looking at the original does not count as rehearsing.
    await tester.drag(find.byType(PageView), const Offset(-600, 0));
    await tester.pump(const Duration(seconds: 40));
    expect(api.calls, isNot(contains(practice)));

    await tester.drag(find.byType(PageView), const Offset(600, 0));
    await tester.pumpAndSettle();
    for (var i = 0; i < rehearsalSeconds('I found the way home.'); i++) {
      await tester.pump(const Duration(seconds: 1));
    }
    await _settle(tester);

    expect(api.calls.where((c) => c == practice), hasLength(1));
    expect(find.text(_en.detailRehearsalDone), findsOneWidget);
  });

  testWidgets('a finished session asks for the intensity rating', (
    tester,
  ) async {
    final (app, api, _) = await _app(auth: FakeAuth(userId: 'user-1'));
    _tallPhone(tester);
    await tester.pumpWidget(app);
    await _settle(tester);

    await tester.tap(find.text('Session'));
    await _settle(tester);
    int loads(String path) => api.calls.where((c) => c == 'GET $path').length;
    const reloaded = [
      '/api/v1/chats',
      '/api/v1/dreams',
      '/api/v1/progress/stats',
    ];
    final before = [for (final path in reloaded) loads(path)];
    await tester.enterText(find.byType(TextField).last, 'yes, done');
    await _settle(tester);
    await tester.tap(find.byIcon(Icons.send_rounded));
    await _settle(tester);

    expect(find.text('Thank you for this session.'), findsOneWidget);
    // The session created a dream: sessions, journal and progress reload.
    expect(
      [for (final path in reloaded) loads(path)],
      [
        for (final n in before) n + 1,
      ],
    );
    expect(find.text(_en.chatIntensityPrompt), findsOneWidget);
    await tester.tap(find.widgetWithText(FilledButton, _en.submit));
    await _settle(tester);

    expect(api.calls, contains('PUT /api/v1/chats/7/intensity'));
    expect(find.text(_en.chatIntensityPrompt), findsNothing);
    expect(find.text(_en.chatSessionDoneCta), findsOneWidget);
  });
}

/// Whether an onboarding arrow is visible (hidden arrows keep their space).
bool _arrowShown(WidgetTester tester, IconData icon) => tester
    .widget<Visibility>(
      find.ancestor(of: find.byIcon(icon), matching: find.byType(Visibility)),
    )
    .visible;
