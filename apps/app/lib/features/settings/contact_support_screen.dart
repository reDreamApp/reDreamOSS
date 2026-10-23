import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';
import 'package:redream_app/core/server/server_config.dart';
import 'package:redream_app/core/widgets/app_button.dart';
import 'package:redream_app/core/widgets/feedback.dart';
import 'package:redream_app/features/server/operator_links.dart';
import 'package:redream_app/features/settings/_form_page.dart';
import 'package:redream_app/l10n/generated/app_localizations.dart';

/// How to reach whoever runs this server (`/instance` operator). Settings
/// only offers it when the server names a contact.
class ContactSupportScreen extends StatelessWidget {
  const ContactSupportScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final theme = Theme.of(context);
    final instance = context.watch<ServerConfig>().instance;
    final contact = instance?.operatorContact ?? '';
    final name = instance?.operatorName;
    final link = contactUri(contact);
    return FormPage(
      title: l10n.contactTitle,
      icon: Icons.support_agent_outlined,
      heading: l10n.contactTitle,
      body: l10n.contactBody,
      children: [
        // A refresh can take the contact away while the screen is open.
        if (contact.isNotEmpty) ...[
          if (name != null) ...[
            Text(l10n.contactOperator(name), style: theme.textTheme.labelLarge),
            const SizedBox(height: 6),
          ],
          Container(
            width: double.infinity,
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: theme.colorScheme.surfaceContainerHighest,
              borderRadius: BorderRadius.circular(12),
            ),
            child: SelectableText(contact),
          ),
          const SizedBox(height: 16),
          if (link != null) ...[
            AppButton(
              label: link.isScheme('mailto')
                  ? l10n.contactOpenEmail
                  : l10n.contactOpenWebsite,
              icon: link.isScheme('mailto')
                  ? Icons.mail_outline
                  : Icons.open_in_new,
              onPressed: () => openOperatorLink(context, link),
            ),
            const SizedBox(height: 8),
          ],
          AppButton(
            label: l10n.contactCopy,
            icon: Icons.copy_outlined,
            style: AppButtonStyle.outlined,
            onPressed: () async {
              await Clipboard.setData(ClipboardData(text: contact));
              if (context.mounted) showAppSnackBar(context, l10n.contactCopied);
            },
          ),
        ],
      ],
    );
  }
}
