import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:redream_app/core/auth/auth_service.dart';
import 'package:redream_app/core/server/server_config.dart';
import 'package:redream_app/core/theme/app_theme.dart';
import 'package:redream_app/core/widgets/app_button.dart';
import 'package:redream_app/core/widgets/app_card.dart';
import 'package:redream_app/core/widgets/brand_logo.dart';
import 'package:redream_app/core/widgets/feedback.dart';
import 'package:redream_app/features/consent/consent_state.dart';
import 'package:redream_app/features/server/operator_links.dart';
import 'package:redream_app/l10n/generated/app_localizations.dart';

/// Blocks the signed-in app until Art. 9 consent is granted for the current
/// consent-text version. Expects a [ConsentState] provider in scope.
class ConsentGate extends StatefulWidget {
  const ConsentGate({required this.child, super.key});

  final Widget child;

  @override
  State<ConsentGate> createState() => _ConsentGateState();
}

class _ConsentGateState extends State<ConsentGate> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) unawaited(context.read<ConsentState>().load());
    });
  }

  @override
  Widget build(BuildContext context) {
    final consent = context.watch<ConsentState>();
    return switch (consent.consentRequired) {
      false => widget.child,
      true => const ConsentScreen(),
      null => _ConsentLoading(
        failed: !consent.loading && consent.errorMessage != null,
        onRetry: consent.load,
      ),
    };
  }
}

class _ConsentLoading extends StatelessWidget {
  const _ConsentLoading({required this.failed, required this.onRetry});

  final bool failed;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      body: Center(
        child: failed
            ? Padding(
                padding: const EdgeInsets.all(24),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(l10n.consentLoadError, textAlign: TextAlign.center),
                    const SizedBox(height: 16),
                    AppButton(label: l10n.retry, onPressed: onRetry),
                  ],
                ),
              )
            : const CircularProgressIndicator(),
      ),
    );
  }
}

/// The explicit-consent screen (GDPR Art. 9(2)(a)): names what is processed
/// and by whom, requires an active opt-in, and offers sign-out to decline.
/// Wording lives in the .arb files; a material change also requires bumping
/// the backend's consent version.
class ConsentScreen extends StatefulWidget {
  const ConsentScreen({super.key});

  @override
  State<ConsentScreen> createState() => _ConsentScreenState();
}

class _ConsentScreenState extends State<ConsentScreen> {
  bool _agreed = false;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final privacy = webUri(
      context.watch<ServerConfig>().instance?.privacyUrl ?? '',
    );
    final consent = context.watch<ConsentState>();
    final brand = BrandTheme.of(context);
    final notice = consent.hasCurrentText
        ? consent.errorMessage
        : l10n.consentUpdateRequired;

    return Scaffold(
      body: SafeArea(
        child: Column(
          children: [
            Expanded(
              child: ListView(
                padding: const EdgeInsets.fromLTRB(20, 24, 20, 12),
                children: [
                  const Center(child: BrandLogo(height: 44)),
                  const SizedBox(height: 20),
                  Text(
                    l10n.consentTitle,
                    textAlign: TextAlign.center,
                    style: const TextStyle(
                      fontSize: 24,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  const SizedBox(height: 12),
                  Text(
                    l10n.consentIntro,
                    style: TextStyle(color: brand.muted, height: 1.4),
                  ),
                  const SizedBox(height: 16),
                  AppCard(
                    child: Column(
                      children: [
                        _ConsentPoint(
                          icon: Icons.nights_stay_outlined,
                          text: l10n.consentBulletData,
                        ),
                        _ConsentPoint(
                          icon: Icons.dns_outlined,
                          text: l10n.consentBulletProcessors(
                            context.watch<ServerConfig>().address,
                          ),
                        ),
                        _ConsentPoint(
                          icon: Icons.tune_outlined,
                          text: l10n.consentBulletControl,
                          last: true,
                        ),
                      ],
                    ),
                  ),
                  if (privacy != null)
                    Align(
                      alignment: Alignment.centerLeft,
                      child: TextButton(
                        onPressed: () => openOperatorLink(context, privacy),
                        child: Text(l10n.settingsPrivacyPolicy),
                      ),
                    ),
                ],
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 0, 20, 12),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  // Above the checkbox, not after the text, so it is seen
                  // without scrolling.
                  if (notice != null) ...[
                    InlineNotice(message: notice),
                    const SizedBox(height: 4),
                  ],
                  CheckboxListTile(
                    value: _agreed,
                    onChanged: consent.saving || !consent.hasCurrentText
                        ? null
                        : (v) => setState(() => _agreed = v ?? false),
                    title: Text(l10n.consentCheckbox),
                    controlAffinity: ListTileControlAffinity.leading,
                    contentPadding: EdgeInsets.zero,
                  ),
                  const SizedBox(height: 8),
                  AppButton(
                    label: l10n.consentContinue,
                    busy: consent.saving,
                    onPressed: _agreed && consent.hasCurrentText
                        ? consent.grant
                        : null,
                  ),
                  const SizedBox(height: 8),
                  Text(
                    l10n.consentDeclineHint,
                    textAlign: TextAlign.center,
                    style: TextStyle(fontSize: 12, color: brand.muted),
                  ),
                  TextButton(
                    onPressed: () => context.read<AuthService>().signOut(),
                    child: Text(l10n.consentSignOut),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _ConsentPoint extends StatelessWidget {
  const _ConsentPoint({
    required this.icon,
    required this.text,
    this.last = false,
  });

  final IconData icon;
  final String text;
  final bool last;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.only(bottom: last ? 0 : 14),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, size: 22, color: Theme.of(context).colorScheme.primary),
          const SizedBox(width: 12),
          Expanded(child: Text(text, style: const TextStyle(height: 1.4))),
        ],
      ),
    );
  }
}
