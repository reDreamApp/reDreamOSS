import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';
import 'package:redream_app/core/theme/app_theme.dart';
import 'package:redream_app/core/utils/formatting.dart';
import 'package:redream_app/core/widgets/assistant_sheep.dart';
import 'package:redream_app/core/widgets/feedback.dart';
import 'package:redream_app/features/chat/chat_models.dart';
import 'package:redream_app/features/chat/chat_state.dart';
import 'package:redream_app/l10n/generated/app_localizations.dart';

class MessageBubble extends StatelessWidget {
  const MessageBubble({required this.message, super.key});

  final ChatMessage message;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final brand = BrandTheme.of(context);
    final scheme = Theme.of(context).colorScheme;
    final isUser = message.isUser;
    final failed = context.select<ChatState, bool>(
      (s) => s.failedMessageId == message.id,
    );
    final text = message.content;
    final textColor = isUser ? brand.onUserBubble : scheme.onSurface;

    final bubble = GestureDetector(
      onLongPress: () => _showActions(context),
      child: Container(
        constraints: BoxConstraints(
          maxWidth: MediaQuery.sizeOf(context).width * 0.78,
        ),
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 10),
        decoration: BoxDecoration(
          color: isUser ? brand.userBubble : brand.assistantBubble,
          borderRadius: BorderRadius.only(
            topLeft: const Radius.circular(18),
            topRight: const Radius.circular(18),
            bottomLeft: Radius.circular(isUser ? 18 : 6),
            bottomRight: Radius.circular(isUser ? 6 : 18),
          ),
          border: failed ? Border.all(color: scheme.error, width: 1.5) : null,
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              text,
              style: TextStyle(fontSize: 15, height: 1.4, color: textColor),
            ),
            const SizedBox(height: 6),
            Text(
              failed ? l10n.chatNotDelivered : formatTime(message.createdAt),
              style: TextStyle(
                fontSize: 12,
                color: failed ? scheme.error : textColor.withValues(alpha: 0.6),
              ),
            ),
          ],
        ),
      ),
    );

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        mainAxisAlignment: isUser
            ? MainAxisAlignment.end
            : MainAxisAlignment.start,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (!isUser) ...[const _AssistantAvatar(), const SizedBox(width: 8)],
          Flexible(child: bubble),
          if (isUser) ...[const SizedBox(width: 8), const _UserAvatar()],
        ],
      ),
    );
  }

  Future<void> _showActions(BuildContext context) async {
    final l10n = AppLocalizations.of(context)!;
    await showModalBottomSheet<void>(
      context: context,
      showDragHandle: true,
      builder: (sheet) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              leading: const Icon(Icons.copy),
              title: Text(l10n.chatCopy),
              onTap: () async {
                await Clipboard.setData(ClipboardData(text: message.content));
                if (!sheet.mounted) return;
                Navigator.pop(sheet);
                if (context.mounted) showAppSnackBar(context, l10n.chatCopied);
              },
            ),
          ],
        ),
      ),
    );
  }
}

class _AssistantAvatar extends StatelessWidget {
  const _AssistantAvatar();

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      width: 36,
      height: 36,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: scheme.primaryContainer,
        shape: BoxShape.circle,
      ),
      child: const AssistantSheep(),
    );
  }
}

class _UserAvatar extends StatelessWidget {
  const _UserAvatar();

  @override
  Widget build(BuildContext context) {
    final brand = BrandTheme.of(context);
    return Container(
      width: 36,
      height: 36,
      decoration: BoxDecoration(
        color: brand.inverseSurface,
        shape: BoxShape.circle,
      ),
      child: Icon(
        Icons.person_outline,
        size: 20,
        color: brand.onInverseSurface,
      ),
    );
  }
}
