import 'dart:async';

import 'package:flutter/material.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:provider/provider.dart';
import 'package:redream_app/core/auth/auth_service.dart';
import 'package:redream_app/core/server/server_config.dart';
import 'package:redream_app/core/settings/app_settings.dart';
import 'package:redream_app/core/theme/app_theme.dart';
import 'package:redream_app/core/utils/errors.dart';
import 'package:redream_app/core/widgets/app_card.dart';
import 'package:redream_app/core/widgets/feedback.dart';
import 'package:redream_app/core/widgets/icon_row.dart';
import 'package:redream_app/core/widgets/theme_mode_toggle.dart';
import 'package:redream_app/features/consent/consent_state.dart';
import 'package:redream_app/features/reminders/reminder_tile.dart';
import 'package:redream_app/features/server/operator_links.dart';
import 'package:redream_app/features/server/server_gate.dart';
import 'package:redream_app/features/settings/change_email_screen.dart';
import 'package:redream_app/features/settings/change_password_screen.dart';
import 'package:redream_app/features/settings/contact_support_screen.dart';
import 'package:redream_app/features/settings/delete_account_screen.dart';
import 'package:redream_app/features/settings/export_data_screen.dart';
import 'package:redream_app/features/settings/profile.dart';
import 'package:redream_app/features/settings/profile_labels.dart';
import 'package:redream_app/features/settings/profile_state.dart';
import 'package:redream_app/l10n/generated/app_localizations.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) unawaited(context.read<ProfileState>().load());
    });
  }

  void _push(Widget screen) =>
      Navigator.push(context, MaterialPageRoute<void>(builder: (_) => screen));

  Future<void> _editFrequency() async {
    final l10n = AppLocalizations.of(context)!;
    final profile = context.read<ProfileState>();
    final picked = await showModalBottomSheet<String>(
      context: context,
      showDragHandle: true,
      builder: (ctx) => _FrequencySheet(
        title: l10n.settingsUpdateFrequency,
        initial: profile.profile.nightmareFrequency,
      ),
    );
    if (picked == null || !mounted) return;
    try {
      await profile.updateNightmareFrequency(picked);
    } on Exception catch (e) {
      if (mounted) {
        showAppSnackBar(context, describeError(e, l10n), error: true);
      }
    }
  }

  Future<void> _signOut() async {
    final auth = context.read<AuthService>();
    Navigator.of(context).popUntil((r) => r.isFirst);
    await auth.signOut();
  }

  Future<void> _changeServer() async {
    final l10n = AppLocalizations.of(context)!;
    final ok = await confirmDialog(
      context,
      title: l10n.changeServerTitle,
      body: l10n.changeServerBody,
      confirmLabel: l10n.serverChange,
      cancelLabel: l10n.cancel,
    );
    if (!ok || !mounted) return;
    final change = changeServer(context);
    Navigator.of(context).popUntil((r) => r.isFirst);
    await change;
  }

  /// Withdrawal is as easy as granting (Art. 7(3)): the consent gate
  /// blocks dream features again, the user's data stays untouched.
  Future<void> _withdrawConsent() async {
    final l10n = AppLocalizations.of(context)!;
    final consent = context.read<ConsentState>();
    final ok = await confirmDialog(
      context,
      title: l10n.withdrawConsentTitle,
      body: l10n.withdrawConsentBody,
      confirmLabel: l10n.withdrawConsentConfirm,
      cancelLabel: l10n.cancel,
      destructive: true,
    );
    if (!ok || !mounted) return;
    if (await consent.withdraw()) {
      if (mounted) Navigator.of(context).popUntil((r) => r.isFirst);
    } else if (mounted) {
      showAppSnackBar(
        context,
        consent.errorMessage ?? l10n.errorUnknown,
        error: true,
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final theme = Theme.of(context);
    final scheme = theme.colorScheme;
    final auth = context.watch<AuthService>();
    final settings = context.watch<AppSettings>();
    final profile = context.watch<ProfileState>();
    final instance = context.watch<ServerConfig>().instance;
    final contact = instance?.operatorContact;
    final privacy = webUri(instance?.privacyUrl ?? '');

    final frequency = profile.profile.nightmareFrequency;
    final frequencyText = profile.loading && !profile.loaded
        ? l10n.loading
        : frequency == null
        ? l10n.settingsNotProvided
        : profileLabel(frequency, l10n);

    const violet = BrandColors.violet;
    const green = BrandColors.deepTeal;
    final teal = scheme.primary;
    final red = scheme.error;

    return Scaffold(
      appBar: AppBar(
        title: Row(
          children: [
            const Icon(Icons.person_outline),
            const SizedBox(width: 8),
            Flexible(
              child: Text(l10n.settingsTitle, overflow: TextOverflow.ellipsis),
            ),
          ],
        ),
      ),
      body: RefreshIndicator(
        onRefresh: profile.refresh,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 32),
          children: [
            _Section(
              icon: Icons.person_outline,
              title: l10n.settingsProfileSection,
              color: teal,
              children: [
                IconRow(
                  icon: Icons.mail_outline,
                  color: teal,
                  title: l10n.settingsEmail,
                  subtitle: auth.email ?? l10n.settingsEmailError,
                  trailing: Icon(Icons.edit_outlined, color: teal),
                  onTap: () => _push(const ChangeEmailScreen()),
                ),
                IconRow(
                  icon: Icons.lock_outline,
                  color: teal,
                  title: l10n.settingsPassword,
                  subtitle: '**********',
                  trailing: Icon(Icons.edit_outlined, color: teal),
                  onTap: () => _push(const ChangePasswordScreen()),
                ),
                IconRow(
                  icon: Icons.nightlight_outlined,
                  color: teal,
                  title: l10n.settingsNightmareFrequency,
                  subtitle: frequencyText,
                  trailing: Icon(Icons.edit_outlined, color: teal),
                  onTap: _editFrequency,
                ),
                if (profile.error case final err?)
                  Padding(
                    padding: const EdgeInsets.all(16),
                    child: Text(
                      describeError(err, l10n),
                      style: TextStyle(color: red),
                    ),
                  ),
              ],
            ),
            const SizedBox(height: 16),
            _Section(
              icon: Icons.settings_outlined,
              title: l10n.settingsAppSection,
              color: violet,
              children: [
                IconRow(
                  icon: Icons.language_outlined,
                  color: violet,
                  title: l10n.settingsLanguage,
                  trailing: DropdownButton<String>(
                    value: settings.language,
                    underline: const SizedBox.shrink(),
                    items: [
                      DropdownMenuItem(
                        value: 'en',
                        child: Text(l10n.languageEnglish),
                      ),
                      DropdownMenuItem(
                        value: 'de',
                        child: Text(l10n.languageGerman),
                      ),
                    ],
                    onChanged: (v) {
                      if (v != null) unawaited(settings.setLanguage(v));
                    },
                  ),
                ),
                IconRow(
                  icon: Icons.brightness_6_outlined,
                  color: violet,
                  title: l10n.settingsTheme,
                  trailing: const ThemeModeToggle(),
                ),
                const ReminderTile(color: violet),
                IconRow(
                  icon: Icons.dns_outlined,
                  color: violet,
                  title: l10n.settingsServer,
                  subtitle: context.select<ServerConfig, String>(
                    (s) => s.address,
                  ),
                  trailing: const RowChevron(),
                  onTap: _changeServer,
                ),
              ],
            ),
            const SizedBox(height: 16),
            _Section(
              icon: Icons.privacy_tip_outlined,
              title: l10n.settingsPrivacySection,
              color: red,
              children: [
                IconRow(
                  icon: Icons.download_outlined,
                  color: red,
                  title: l10n.settingsExport,
                  trailing: const RowChevron(),
                  onTap: () => _push(const ExportDataScreen()),
                ),
                IconRow(
                  icon: Icons.gpp_maybe_outlined,
                  color: red,
                  title: l10n.settingsWithdrawConsent,
                  subtitle: l10n.settingsWithdrawConsentSubtitle,
                  onTap: _withdrawConsent,
                ),
                Padding(
                  padding: const EdgeInsets.all(16),
                  child: OutlinedButton.icon(
                    style: OutlinedButton.styleFrom(
                      foregroundColor: red,
                      side: BorderSide(color: red),
                    ),
                    onPressed: () => _push(const DeleteAccountScreen()),
                    icon: const Icon(Icons.no_accounts_outlined),
                    label: Text(l10n.settingsDeleteAccount),
                  ),
                ),
              ],
            ),
            if (contact != null || privacy != null) ...[
              const SizedBox(height: 16),
              // Whoever runs this server, as its /instance names them.
              _Section(
                icon: Icons.help_center_outlined,
                title: l10n.settingsSupportSection,
                color: green,
                children: [
                  if (contact != null)
                    IconRow(
                      icon: Icons.support_agent_outlined,
                      color: green,
                      title: l10n.settingsContact,
                      subtitle: contact,
                      trailing: const RowChevron(),
                      onTap: () => _push(const ContactSupportScreen()),
                    ),
                  if (privacy != null)
                    IconRow(
                      icon: Icons.policy_outlined,
                      color: green,
                      title: l10n.settingsPrivacyPolicy,
                      trailing: const Icon(Icons.open_in_new),
                      onTap: () => openOperatorLink(context, privacy),
                    ),
                ],
              ),
            ],
            const SizedBox(height: 24),
            Text(
              l10n.appTitle,
              textAlign: TextAlign.center,
              style: theme.textTheme.titleMedium,
            ),
            const _VersionLabel(),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 8),
              child: Text(
                l10n.settingsTagline,
                textAlign: TextAlign.center,
                style: theme.textTheme.bodySmall,
              ),
            ),
            const SizedBox(height: 8),
            AppCard(
              padding: EdgeInsets.zero,
              child: IconRow(
                icon: Icons.logout,
                color: red,
                title: l10n.settingsSignOut,
                titleColor: red,
                trailing: const RowChevron(),
                onTap: _signOut,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// A card of rows under a header band tinted in the section's colour.
class _Section extends StatelessWidget {
  const _Section({
    required this.icon,
    required this.title,
    required this.color,
    required this.children,
  });

  final IconData icon;
  final String title;
  final Color color;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final dark = theme.brightness == Brightness.dark;
    return AppCard(
      padding: EdgeInsets.zero,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          ColoredBox(
            color: Color.alphaBlend(
              color.withValues(alpha: dark ? 0.22 : 0.13),
              theme.colorScheme.surface,
            ),
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
              child: Row(
                children: [
                  Icon(icon, color: color),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text(
                      title,
                      style: theme.textTheme.titleMedium?.copyWith(
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
          for (final child in children) ...[const Divider(height: 1), child],
        ],
      ),
    );
  }
}

class _FrequencySheet extends StatefulWidget {
  const _FrequencySheet({required this.title, this.initial});

  final String title;
  final String? initial;

  @override
  State<_FrequencySheet> createState() => _FrequencySheetState();
}

class _FrequencySheetState extends State<_FrequencySheet> {
  String? _value;

  @override
  void initState() {
    super.initState();
    _value = widget.initial;
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return SafeArea(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(widget.title, style: Theme.of(context).textTheme.titleLarge),
          const SizedBox(height: 8),
          RadioGroup<String>(
            groupValue: _value,
            onChanged: (v) => setState(() => _value = v),
            child: Column(
              children: [
                for (final f in Profile.nightmareFrequencies)
                  RadioListTile<String>(
                    value: f,
                    title: Text(profileLabel(f, l10n)),
                  ),
              ],
            ),
          ),
          Padding(
            padding: const EdgeInsets.all(16),
            child: FilledButton(
              onPressed: _value == null
                  ? null
                  : () => Navigator.pop(context, _value),
              child: Text(l10n.save),
            ),
          ),
        ],
      ),
    );
  }
}

class _VersionLabel extends StatelessWidget {
  const _VersionLabel();

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return FutureBuilder<PackageInfo>(
      future: PackageInfo.fromPlatform(),
      builder: (context, snap) {
        final info = snap.data;
        final version = info == null
            ? '…'
            : '${info.version}+${info.buildNumber}';
        return Text(
          l10n.settingsVersion(version),
          textAlign: TextAlign.center,
          style: Theme.of(context).textTheme.bodySmall,
        );
      },
    );
  }
}
