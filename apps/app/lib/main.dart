import 'dart:async';
import 'dart:ui';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:redream_app/app.dart';
import 'package:redream_app/core/api/api_client.dart';
import 'package:redream_app/core/app_services.dart';
import 'package:redream_app/core/auth/auth_service.dart';
import 'package:redream_app/core/auth/session_store.dart';
import 'package:redream_app/core/auth/token_auth_service.dart';
import 'package:redream_app/core/env.dart';
import 'package:redream_app/core/settings/app_settings.dart';
import 'package:redream_app/core/storage/local_store.dart';
import 'package:redream_app/core/storage/secure_audio_cache.dart';
import 'package:redream_app/features/chat/voice_recorder.dart';
import 'package:redream_app/features/reminders/notification_service.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await SecureAudioCache.removeLegacyPlaintext();
  await Env.load();
  // Read before runApp, so the first frame already knows who is signed in.
  await SessionStore.removeLegacy();
  final sessions = SessionStore();
  final session = await sessions.read();

  final store = await LocalStore.open();
  late final TokenAuthService auth;
  final api = ApiClient(
    baseUrl: Env.apiBaseUrl,
    tokenProvider: () => auth.accessToken,
    onUnauthorized: (token) => auth.onUnauthorized(token),
  );
  auth = TokenAuthService(
    api,
    sessions,
    session,
    StoredSession.originOf(Env.apiBaseUrl),
  );
  unawaited(auth.validate());
  final settings = AppSettings(
    store,
    platformLanguage: PlatformDispatcher.instance.locale.languageCode,
  );
  final notifications = NotificationService();
  await notifications.initialize();

  final services = AppServices(
    api: api,
    store: store,
    notifications: notifications,
    voiceRecorder: RecordVoiceRecorder.new,
  );

  runApp(
    MultiProvider(
      providers: [
        ChangeNotifierProvider<AuthService>.value(value: auth),
        ChangeNotifierProvider<AppSettings>.value(value: settings),
        Provider<AppServices>.value(value: services),
      ],
      child: const ReDreamApp(),
    ),
  );
}
