import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'package:redream_app/core/settings/app_settings.dart';
import 'package:redream_app/core/theme/app_theme.dart';
import 'package:redream_app/core/widgets/brand_logo.dart';
import 'package:redream_app/core/widgets/theme_mode_toggle.dart';
import 'package:redream_app/l10n/generated/app_localizations.dart';

final _emailPattern = RegExp(r'^[^\s@]+@[^\s@]+\.[^\s@]{2,}$');

String? validateEmail(String? value, AppLocalizations l10n) {
  final v = value?.trim() ?? '';
  if (v.isEmpty) return l10n.validationRequired(l10n.signInEmail);
  if (!_emailPattern.hasMatch(v)) return l10n.validationEmailInvalid;
  return null;
}

/// The server's minimum for a new password.
const passwordMinLength = 8;

String? validatePassword(
  String? value,
  AppLocalizations l10n, {
  int min = passwordMinLength,
}) {
  final v = value ?? '';
  if (v.isEmpty) return l10n.validationRequired(l10n.signInPassword);
  if (v.length < min) return l10n.validationPasswordMin(min);
  return null;
}

String? validateConfirm(String? value, String password, AppLocalizations l10n) {
  final v = value ?? '';
  if (v.isEmpty) return l10n.validationRequired(l10n.signUpConfirmPassword);
  if (v != password) return l10n.passwordsDoNotMatch;
  return null;
}

class AuthEmailField extends StatelessWidget {
  const AuthEmailField({
    required this.controller,
    super.key,
    this.label,
    this.hint,
    this.validator,
    this.autofocus = false,
    this.isUsername = true,
    this.textInputAction = TextInputAction.next,
    this.onSubmitted,
  });

  final TextEditingController controller;
  final String? label;
  final String? hint;
  final FormFieldValidator<String>? validator;
  final bool autofocus;
  final bool isUsername;
  final TextInputAction textInputAction;
  final ValueChanged<String>? onSubmitted;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return TextFormField(
      controller: controller,
      autofocus: autofocus,
      keyboardType: TextInputType.emailAddress,
      autocorrect: false,
      textInputAction: textInputAction,
      autofillHints: [
        if (isUsername) AutofillHints.username else AutofillHints.email,
      ],
      validator: validator ?? (v) => validateEmail(v, l10n),
      onFieldSubmitted: onSubmitted,
      decoration: InputDecoration(
        labelText: label ?? l10n.signInEmail,
        hintText: hint ?? l10n.signInEmailHint,
        prefixIcon: const Icon(Icons.mail_outline),
      ),
    );
  }
}

class AuthPasswordField extends StatefulWidget {
  const AuthPasswordField({
    required this.controller,
    super.key,
    this.label,
    this.hint,
    this.validator,
    this.isNewPassword = false,
    this.textInputAction = TextInputAction.done,
    this.onSubmitted,
  });

  final TextEditingController controller;
  final String? label;
  final String? hint;
  final FormFieldValidator<String>? validator;
  final bool isNewPassword;
  final TextInputAction textInputAction;
  final ValueChanged<String>? onSubmitted;

  @override
  State<AuthPasswordField> createState() => _AuthPasswordFieldState();
}

class _AuthPasswordFieldState extends State<AuthPasswordField> {
  bool _obscure = true;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return TextFormField(
      controller: widget.controller,
      obscureText: _obscure,
      keyboardType: TextInputType.visiblePassword,
      enableSuggestions: false,
      autocorrect: false,
      textInputAction: widget.textInputAction,
      autofillHints: [
        if (widget.isNewPassword)
          AutofillHints.newPassword
        else
          AutofillHints.password,
      ],
      // Only a new password has a length rule; one to check just needs input.
      validator:
          widget.validator ??
          (v) => validatePassword(
            v,
            l10n,
            min: widget.isNewPassword ? passwordMinLength : 1,
          ),
      onFieldSubmitted: widget.onSubmitted,
      decoration: InputDecoration(
        labelText: widget.label ?? l10n.signInPassword,
        hintText: widget.hint ?? l10n.signInPasswordHint,
        prefixIcon: const Icon(Icons.lock_outline),
        suffixIcon: IconButton(
          icon: Icon(_obscure ? Icons.visibility_off : Icons.visibility),
          onPressed: () => setState(() => _obscure = !_obscure),
        ),
      ),
    );
  }
}

/// Logo + gradient slogan shown on top of every auth page.
class AuthHeader extends StatelessWidget {
  const AuthHeader({super.key});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Column(
      children: [
        const BrandLogo(height: 140),
        const SizedBox(height: 12),
        ShaderMask(
          shaderCallback: (bounds) =>
              BrandTheme.of(context).headlineGradient.createShader(bounds),
          child: Text(
            l10n.authSlogan,
            textAlign: TextAlign.center,
            style: const TextStyle(
              fontSize: 26,
              fontWeight: FontWeight.w800,
              color: Colors.white,
            ),
          ),
        ),
      ],
    );
  }
}

/// Theme toggle + language menu for pages outside the shell.
class AuthTopBar extends StatelessWidget {
  const AuthTopBar({super.key, this.showBack = false});

  final bool showBack;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final settings = context.watch<AppSettings>();
    return Row(
      children: [
        if (showBack)
          IconButton(
            icon: const Icon(Icons.arrow_back),
            onPressed: () => Navigator.of(context).maybePop(),
          ),
        const Spacer(),
        const ThemeModeToggle(),
        PopupMenuButton<String>(
          icon: const Icon(Icons.language_outlined),
          initialValue: settings.language,
          onSelected: settings.setLanguage,
          itemBuilder: (_) => [
            PopupMenuItem(value: 'en', child: Text(l10n.languageEnglish)),
            PopupMenuItem(value: 'de', child: Text(l10n.languageGerman)),
          ],
        ),
      ],
    );
  }
}
