import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:redream_app/core/api/api_exception.dart';
import 'package:redream_app/core/server/server_config.dart';
import 'package:redream_app/core/storage/local_store.dart';
import 'package:redream_app/core/theme/app_theme.dart';
import 'package:redream_app/core/widgets/feedback.dart';
import 'package:redream_app/features/consent/consent_screen.dart';
import 'package:redream_app/features/consent/consent_service.dart';
import 'package:redream_app/features/consent/consent_state.dart';
import 'package:redream_app/l10n/generated/app_localizations.dart';
import 'package:redream_app/l10n/generated/app_localizations_en.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../../fake_api.dart';

final _en = AppLocalizationsEn();

class _FakeConsentService implements IConsentService {
  _FakeConsentService({this.requiredVersion = consentTextVersion});

  final String requiredVersion;
  ApiException? grantError;

  @override
  Future<ConsentStatus> status() async => ConsentStatus(
    requiredVersion: requiredVersion,
    grantedVersion: null,
    consentRequired: true,
  );

  @override
  Future<void> grant(String version) async {
    if (grantError case final error?) throw error;
  }

  @override
  Future<void> withdraw() async {}
}

/// The consent screen on a phone-sized display, on a server that names
/// [privacyUrl] as its privacy policy.
Future<void> _pumpConsent(
  WidgetTester tester,
  _FakeConsentService service, {
  String? privacyUrl,
}) async {
  tester.view
    ..physicalSize = const Size(1080, 2340)
    ..devicePixelRatio = 3;
  addTearDown(tester.view.reset);
  final state = ConsentState(service);
  await state.load();
  FlutterSecureStorage.setMockInitialValues({});
  SharedPreferences.setMockInitialValues({});
  final api = FakeApi()
    ..on(
      'GET /api/v1/instance',
      (_, _) => instanceJson(privacyUrl: privacyUrl),
    );
  final server = ServerConfig(
    await LocalStore.open(),
    consentVersion: consentTextVersion,
    httpClient: api.httpClient(),
  );
  await server.connect('http://fake');
  await tester.pumpWidget(
    MultiProvider(
      providers: [
        ChangeNotifierProvider<ConsentState>.value(value: state),
        ChangeNotifierProvider<ServerConfig>.value(value: server),
      ],
      child: MaterialApp(
        theme: AppTheme.light,
        locale: const Locale('en'),
        localizationsDelegates: AppLocalizations.localizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: const ConsentScreen(),
      ),
    ),
  );
  await tester.pumpAndSettle();
}

/// The notice is on screen without scrolling.
void _expectNoticeVisible(WidgetTester tester, String text) {
  final message = find.text(text);
  expect(message, findsOneWidget);
  expect(
    find.ancestor(of: message, matching: find.byType(InlineNotice)),
    findsOneWidget,
  );
  final screen =
      Offset.zero & tester.view.physicalSize / tester.view.devicePixelRatio;
  final rect = tester.getRect(message);
  expect(
    screen.contains(rect.topLeft) && screen.contains(rect.bottomRight),
    isTrue,
  );
}

void main() {
  testWidgets('without a privacy URL the server shows no policy link', (
    tester,
  ) async {
    await _pumpConsent(tester, _FakeConsentService());
    await tester.drag(find.byType(Scrollable).first, const Offset(0, -3000));
    await tester.pumpAndSettle();
    expect(find.text(_en.consentBulletControl), findsOneWidget);
    expect(find.text('Privacy policy'), findsNothing);
  });

  testWidgets("the policy link is the server's own", (tester) async {
    await _pumpConsent(
      tester,
      _FakeConsentService(),
      privacyUrl: 'https://dreams.example.org/privacy',
    );
    await tester.scrollUntilVisible(find.text('Privacy policy'), 200);
    expect(find.text('Privacy policy'), findsOneWidget);
  });

  testWidgets('an outdated consent text shows the update notice in view', (
    tester,
  ) async {
    await _pumpConsent(
      tester,
      _FakeConsentService(requiredVersion: '2099-01-01'),
    );

    _expectNoticeVisible(
      tester,
      'Update the app to review and accept the current consent text.',
    );
  });

  testWidgets('a refused grant shows the server message in view', (
    tester,
  ) async {
    const message = 'Please install the latest version of the app to continue.';
    final service = _FakeConsentService()
      ..grantError = ApiException(426, message);
    await _pumpConsent(tester, service);
    expect(find.byType(InlineNotice), findsNothing);

    await tester.tap(find.byType(Checkbox));
    await tester.pump();
    await tester.tap(find.text('Agree and continue'));
    await tester.pumpAndSettle();

    _expectNoticeVisible(tester, message);
  });
}
