import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:redream_app/app.dart';
import 'package:redream_app/core/api/api_client.dart';
import 'package:redream_app/core/app_services.dart';
import 'package:redream_app/core/auth/auth_service.dart';
import 'package:redream_app/core/auth/personal_key.dart';
import 'package:redream_app/core/auth/session_store.dart';
import 'package:redream_app/core/auth/token_auth_service.dart';
import 'package:redream_app/core/platform/address_bar.dart'
    if (dart.library.js_interop) 'package:redream_app/core/platform/address_bar_web.dart';
import 'package:redream_app/core/platform/platform_features.dart';
import 'package:redream_app/core/server/server_config.dart';
import 'package:redream_app/core/settings/app_settings.dart';
import 'package:redream_app/core/storage/local_store.dart';
import 'package:redream_app/core/storage/secure_audio_cache.dart';
import 'package:redream_app/features/chat/voice_recorder.dart';
import 'package:redream_app/features/consent/consent_service.dart';
import 'package:redream_app/features/reminders/notification_service.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  if (PlatformFeatures.offlineAudio) {
    await SecureAudioCache.removeLegacyPlaintext();
  }
  // Read before runApp, so the first frame already knows the server and who
  // is signed in.
  await SessionStore.removeLegacy();
  final sessions = SessionStore();
  final session = await sessions.read();

  final store = await LocalStore.open();
  // A personal server's link (`make start`) carries its key in the fragment.
  final personalKey = PersonalKey(store);
  if (kIsWeb && await personalKey.takeFrom(Uri.base.fragment)) {
    clearAddressFragment();
  }
  final server = ServerConfig(
    store,
    consentVersion: consentTextVersion,
    // The web app is served by its server, on the same origin.
    pageOrigin: kIsWeb ? Uri.base.origin : null,
  );
  late final TokenAuthService auth;
  final api = ApiClient(
    baseUrl: () => server.baseUrl ?? '',
    tokenProvider: () => auth.accessToken,
    onUnauthorized: (token) => auth.onUnauthorized(token),
  );
  auth = TokenAuthService(
    api,
    sessions,
    session,
    server.url,
    personalKey: () => personalKey.value,
  );
  unawaited(auth.validate());
  unawaited(server.refresh());
  final settings = AppSettings(
    store,
    platformLanguage: PlatformDispatcher.instance.locale.languageCode,
  );
  final notifications = NotificationService();
  if (PlatformFeatures.reminders) await notifications.initialize();

  final services = AppServices(
    api: api,
    store: store,
    notifications: notifications,
    voiceRecorder: RecordVoiceRecorder.new,
  );

  runApp(
    MultiProvider(
      providers: [
        ChangeNotifierProvider<ServerConfig>.value(value: server),
        ChangeNotifierProvider<AuthService>.value(value: auth),
        ChangeNotifierProvider<AppSettings>.value(value: settings),
        Provider<AppServices>.value(value: services),
      ],
      child: const ReDreamApp(),
    ),
  );
}
