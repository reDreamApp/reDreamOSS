import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:redream_app/core/auth/auth_service.dart';
import 'package:redream_app/core/theme/app_theme.dart';
import 'package:redream_app/features/settings/change_email_screen.dart';
import 'package:redream_app/features/settings/change_password_screen.dart';
import 'package:redream_app/l10n/generated/app_localizations.dart';
import 'package:redream_app/l10n/generated/app_localizations_en.dart';

import '../../fake_auth.dart';

final _en = AppLocalizationsEn();

/// [screen] pushed over a placeholder page, as settings does.
Future<void> _open(WidgetTester tester, FakeAuth auth, Widget screen) async {
  tester.view
    ..physicalSize = const Size(1080, 2340)
    ..devicePixelRatio = 3;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(
    ChangeNotifierProvider<AuthService>.value(
      value: auth,
      child: MaterialApp(
        theme: AppTheme.light,
        locale: const Locale('en'),
        localizationsDelegates: AppLocalizations.localizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: Scaffold(
          body: Builder(
            builder: (context) => TextButton(
              onPressed: () => Navigator.push(
                context,
                MaterialPageRoute<void>(builder: (_) => screen),
              ),
              child: const Text('open'),
            ),
          ),
        ),
      ),
    ),
  );
  await tester.tap(find.text('open'));
  await tester.pumpAndSettle();
}

Future<void> _fill(WidgetTester tester, List<String> values) async {
  for (final (i, value) in values.indexed) {
    await tester.enterText(find.byType(TextFormField).at(i), value);
  }
}

FakeAuth _signedIn() => FakeAuth(userId: 'user-1', email: 'me@example.com');

void main() {
  group('change password', () {
    testWidgets('one call with the current password, no new sign-in', (
      tester,
    ) async {
      final auth = _signedIn();
      await _open(tester, auth, const ChangePasswordScreen());

      await _fill(tester, ['old-password', 'new-password', 'new-password']);
      await tester.tap(find.text(_en.changePasswordButton).last);
      await tester.pumpAndSettle();

      expect(auth.passwordChanges, [
        (current: 'old-password', next: 'new-password'),
      ]);
      expect(auth.signIns, isEmpty);
      expect(auth.isSignedIn, isTrue);
      expect(find.text(_en.changePasswordSuccess), findsOneWidget);
      expect(find.text('open'), findsOneWidget);
    });

    testWidgets('a new password needs 8 characters', (tester) async {
      final auth = _signedIn();
      await _open(tester, auth, const ChangePasswordScreen());

      await _fill(tester, ['old', 'seven77', 'seven77']);
      await tester.tap(find.text(_en.changePasswordButton).last);
      await tester.pumpAndSettle();

      expect(find.text(_en.validationPasswordMin(8)), findsOneWidget);
      expect(auth.passwordChanges, isEmpty);
    });

    testWidgets('a wrong current password says so', (tester) async {
      final auth = _signedIn()
        ..error = const AuthFailure(AuthFailureKind.wrongPassword);
      await _open(tester, auth, const ChangePasswordScreen());

      await _fill(tester, ['not-mine', 'new-password', 'new-password']);
      await tester.tap(find.text(_en.changePasswordButton).last);
      await tester.pumpAndSettle();

      expect(find.text(_en.changePasswordWrongCurrent), findsOneWidget);
      expect(auth.isSignedIn, isTrue);
    });
  });

  group('change email', () {
    testWidgets('asks for the current password and changes right away', (
      tester,
    ) async {
      final auth = _signedIn();
      await _open(tester, auth, const ChangeEmailScreen());
      expect(find.text(_en.changeEmailBody), findsOneWidget);

      await _fill(tester, ['Me@Example.com', 'my-password']);
      await tester.tap(find.text(_en.changeEmailButton).last);
      await tester.pumpAndSettle();
      expect(find.text(_en.changeEmailSame), findsOneWidget);

      await _fill(tester, ['new@example.com', '']);
      await tester.tap(find.text(_en.changeEmailButton).last);
      await tester.pumpAndSettle();
      expect(
        find.text(_en.validationRequired(_en.signInPassword)),
        findsOneWidget,
      );

      await _fill(tester, ['new@example.com', 'my-password']);
      await tester.tap(find.text(_en.changeEmailButton).last);
      await tester.pumpAndSettle();

      expect(auth.emailChanges, [
        (current: 'my-password', email: 'new@example.com'),
      ]);
      expect(auth.email, 'new@example.com');
      expect(find.text(_en.changeEmailSuccess), findsOneWidget);
      expect(find.text('open'), findsOneWidget);
    });

    testWidgets('explains a wrong password and an address in use', (
      tester,
    ) async {
      final auth = _signedIn()
        ..error = const AuthFailure(AuthFailureKind.wrongPassword);
      await _open(tester, auth, const ChangeEmailScreen());

      await _fill(tester, ['new@example.com', 'not-mine']);
      await tester.tap(find.text(_en.changeEmailButton).last);
      await tester.pumpAndSettle();
      expect(find.text(_en.changePasswordWrongCurrent), findsOneWidget);

      auth.error = const AuthFailure(AuthFailureKind.emailTaken);
      await tester.tap(find.text(_en.changeEmailButton).last);
      await tester.pumpAndSettle();
      expect(find.text(_en.authEmailTaken), findsOneWidget);
      expect(auth.email, 'me@example.com');
    });
  });
}
