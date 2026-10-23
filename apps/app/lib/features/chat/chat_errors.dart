import 'package:redream_app/features/chat/chat_state.dart';
import 'package:redream_app/l10n/generated/app_localizations.dart';

/// [ChatState.errorMessage] for people: its markers localized, anything
/// else (the server's own words) as it is.
String chatErrorText(String message, AppLocalizations l10n) =>
    switch (message) {
      ChatState.offline => l10n.errorOffline,
      ChatState.streamInterrupted => l10n.chatStreamInterrupted,
      ChatState.noChat => l10n.chatStartErrorBody,
      ChatState.rateLimited => l10n.errorRateLimited,
      ChatState.voiceUnavailable => l10n.voiceUnavailable,
      ChatState.serverError => l10n.errorServer,
      _ => message,
    };
