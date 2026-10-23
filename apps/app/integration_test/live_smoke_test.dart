/// Live smoke test: drives the REAL app against a running stack.
///
/// Prerequisites:
///   - apps/server reachable at the address given as
///     `--dart-define=DEFAULT_SERVER_URL=...` (http://10.0.2.2:8001 from the
///     Android emulator); on a fresh start the app connects to it by itself
///   - a test user made with the server's operator CLI
///     (`python -m redream_api.cli create-user test@local.dev`), by default
///     test@local.dev / Test-12345-chat
///     (override with --dart-define=SMOKE_EMAIL=... --dart-define=SMOKE_PASSWORD=...)
///
/// Run: `fvm flutter test integration_test/live_smoke_test.dart -d <device>`
/// with `--dart-define=DEFAULT_SERVER_URL=http://10.0.2.2:8001`.
///
/// It makes real LLM calls, so it is NOT part of `flutter test` CI.
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:provider/provider.dart';
import 'package:redream_app/core/auth/auth_service.dart';
import 'package:redream_app/features/auth/sign_in_screen.dart';
import 'package:redream_app/features/chat/chat_screen.dart';
import 'package:redream_app/features/chat/chat_state.dart';
import 'package:redream_app/features/consent/consent_screen.dart';
import 'package:redream_app/features/shell/main_shell.dart';
import 'package:redream_app/features/shell/shell_tabs.dart';
import 'package:redream_app/main.dart' as app;
import 'package:shared_preferences/shared_preferences.dart';

const _email = String.fromEnvironment(
  'SMOKE_EMAIL',
  defaultValue: 'test@local.dev',
);
const _password = String.fromEnvironment(
  'SMOKE_PASSWORD',
  defaultValue: 'Test-12345-chat',
);

/// Pumps until [condition] holds, failing after [timeout].
Future<void> pumpUntil(
  WidgetTester tester,
  bool Function() condition, {
  required String description,
  Duration timeout = const Duration(seconds: 30),
}) async {
  final deadline = DateTime.now().add(timeout);
  while (!condition()) {
    if (DateTime.now().isAfter(deadline)) {
      fail('timed out after $timeout waiting for: $description');
    }
    await tester.pump(const Duration(milliseconds: 250));
  }
}

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('sign in, consent, chat turn with a streamed AI reply', (
    tester,
  ) async {
    // In-memory prefs keep settings hermetic; a stored session is signed out
    // below.
    SharedPreferences.setMockInitialValues(<String, Object>{});
    await app.main();
    await tester.pump(const Duration(seconds: 1));

    await pumpUntil(
      tester,
      () =>
          tester.any(find.byType(SignInScreen)) ||
          tester.any(find.byType(MainShell)) ||
          tester.any(find.byType(ConsentScreen)),
      description: 'first screen',
    );

    // Always start from a fresh sign-in, so the run also covers login.
    final auth = tester.element(find.byType(MaterialApp)).read<AuthService>();
    if (auth.isSignedIn) {
      await auth.signOut();
      await pumpUntil(
        tester,
        () => tester.any(find.byType(SignInScreen)),
        description: 'sign-in screen after sign-out',
      );
    }

    await tester.enterText(find.byType(TextFormField).at(0), _email);
    await tester.enterText(find.byType(TextFormField).at(1), _password);
    await tester.tap(find.byType(FilledButton).first);
    await pumpUntil(
      tester,
      () =>
          tester.any(find.byType(MainShell)) ||
          tester.any(find.byType(ConsentScreen)),
      description: 'consent screen or shell after sign-in',
    );

    if (tester.any(find.byType(ConsentScreen))) {
      await tester.tap(find.byType(Checkbox));
      await tester.pump();
      await tester.tap(
        find.descendant(
          of: find.byType(ConsentScreen),
          matching: find.byType(FilledButton),
        ),
      );
      await pumpUntil(
        tester,
        () => tester.any(find.byType(MainShell)),
        description: 'shell after granting consent',
      );
    }

    // Switching via ShellTabs opens the session tab, which resumes or starts
    // a chat; poll the state rather than the widget tree.
    tester
        .element(find.byType(MainShell))
        .read<ShellTabs>()
        .select(ShellTab.session);
    final chat = tester.element(find.byType(ChatScreen)).read<ChatState>();
    await pumpUntil(
      tester,
      () => chat.chatId != null || chat.startFailed,
      description: 'chat to start',
    );
    expect(chat.startFailed, isFalse, reason: chat.errorMessage);
    if (chat.isDone) {
      await chat.startChat(language: 'en', welcomeMessages: ['hi']);
      await tester.pump();
    }
    final before = chat.messages.length;

    final composer = find.descendant(
      of: find.byType(ChatScreen),
      matching: find.byType(TextField),
    );
    await pumpUntil(
      tester,
      () => tester.any(composer),
      description: 'composer',
    );
    await tester.enterText(
      composer,
      'I keep dreaming about being chased through my old school.',
    );
    await tester.pump();
    await tester.tap(find.byIcon(Icons.send_rounded));

    await pumpUntil(
      tester,
      () => !chat.sending && chat.messages.length >= before + 2,
      description: 'streamed assistant reply (live AI turn)',
      timeout: const Duration(seconds: 120),
    );
    expect(chat.errorMessage, isNull, reason: 'turn completed without error');
    expect(chat.safetyEvent, isNull, reason: 'a dream is not a crisis');
    expect(chat.messages.last.isUser, isFalse);
    expect(chat.messages.last.content.trim(), isNotEmpty);
    await tester.pump(const Duration(seconds: 1));
  });
}
