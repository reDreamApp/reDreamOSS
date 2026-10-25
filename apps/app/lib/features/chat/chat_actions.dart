import 'package:flutter/widgets.dart';
import 'package:provider/provider.dart';
import 'package:redream_app/core/settings/app_settings.dart';
import 'package:redream_app/features/chat/chat_state.dart';
import 'package:redream_app/l10n/generated/app_localizations.dart';

/// Session entry points shared by the chat screen and the history drawer.
/// They supply the app language and the localized welcome bubbles, which are
/// display-only and never sent to the backend.
extension ChatActions on BuildContext {
  List<String> get _welcome {
    final l10n = AppLocalizations.of(this)!;
    return [l10n.chatWelcome, l10n.chatWelcome2, l10n.chatWelcome3];
  }

  String get _language => read<AppSettings>().language;

  /// First open: resume the latest unfinished chat, else start a new one.
  Future<void> resumeOrStartChat() => read<ChatState>().resumeOrStart(
    language: _language,
    welcomeMessages: _welcome,
  );

  Future<void> startNewChat() => read<ChatState>().startChat(
    language: _language,
    welcomeMessages: _welcome,
  );

  /// Replaces a chat nobody has written in yet when the app's language
  /// changed (see [ChatState.followAppLanguage]).
  Future<void> followAppLanguage() => read<ChatState>().followAppLanguage(
    language: _language,
    welcomeMessages: _welcome,
  );

  Future<bool> openChat(int id) =>
      read<ChatState>().loadChat(id, welcomeMessages: _welcome);

  Future<void> refreshChat() =>
      read<ChatState>().refreshCurrentChat(welcomeMessages: _welcome);
}
