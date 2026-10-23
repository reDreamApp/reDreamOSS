import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';
import 'package:redream_app/core/auth/auth_service.dart';
import 'package:redream_app/core/settings/app_settings.dart';
import 'package:redream_app/core/theme/app_theme.dart';
import 'package:redream_app/core/utils/errors.dart';
import 'package:redream_app/core/widgets/app_button.dart';
import 'package:redream_app/core/widgets/feedback.dart';
import 'package:redream_app/features/auth/auth_fields.dart';
import 'package:redream_app/l10n/generated/app_localizations.dart';

class SignUpScreen extends StatefulWidget {
  const SignUpScreen({super.key});

  @override
  State<SignUpScreen> createState() => _SignUpScreenState();
}

class _SignUpScreenState extends State<SignUpScreen> {
  final _form = GlobalKey<FormState>();
  final _email = TextEditingController();
  final _password = TextEditingController();
  final _confirm = TextEditingController();
  final _invite = TextEditingController();
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _email.dispose();
    _password.dispose();
    _confirm.dispose();
    _invite.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (!_form.currentState!.validate()) return;
    final l10n = AppLocalizations.of(context)!;
    if (_password.text != _confirm.text) {
      setState(() => _error = l10n.passwordsDoNotMatch);
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    final auth = context.read<AuthService>();
    final settings = context.read<AppSettings>();
    final invite = _invite.text.trim();
    try {
      await auth.signUp(
        email: _email.text.trim(),
        password: _password.text,
        inviteCode: invite,
      );
      TextInput.finishAutofillContext();
      // Registering signs in right away; there is no confirmation mail.
      if (auth.userId case final userId?) {
        await settings.markOnboardingPending(userId);
        if (mounted) Navigator.of(context).popUntil((r) => r.isFirst);
      }
    } on AuthFailure catch (e) {
      // An invite-only server refuses a missing code like a wrong one.
      final message = e.kind == AuthFailureKind.inviteInvalid && invite.isEmpty
          ? l10n.authInviteRequired
          : describeError(e, l10n);
      if (mounted) setState(() => _error = message);
    } on Exception catch (e) {
      if (mounted) setState(() => _error = describeError(e, l10n));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(24, 8, 24, 24),
          child: Form(
            key: _form,
            child: AutofillGroup(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const AuthTopBar(showBack: true),
                  const AuthHeader(),
                  const SizedBox(height: 32),
                  AuthEmailField(
                    controller: _email,
                    autofocus: true,
                    isUsername: false,
                  ),
                  const SizedBox(height: 12),
                  AuthPasswordField(
                    controller: _password,
                    isNewPassword: true,
                    textInputAction: TextInputAction.next,
                  ),
                  const SizedBox(height: 12),
                  AuthPasswordField(
                    controller: _confirm,
                    label: l10n.signUpConfirmPassword,
                    isNewPassword: true,
                    validator: (v) => validateConfirm(v, _password.text, l10n),
                    textInputAction: TextInputAction.next,
                  ),
                  const SizedBox(height: 12),
                  TextFormField(
                    controller: _invite,
                    autocorrect: false,
                    enableSuggestions: false,
                    textInputAction: TextInputAction.done,
                    onFieldSubmitted: (_) => _submit(),
                    decoration: InputDecoration(
                      labelText: l10n.signUpInviteCode,
                      helperText: l10n.signUpInviteCodeHint,
                      helperMaxLines: 2,
                      prefixIcon: const Icon(Icons.vpn_key_outlined),
                    ),
                  ),
                  const SizedBox(height: 16),
                  if (_error != null) ...[
                    InlineNotice(message: _error!),
                    const SizedBox(height: 16),
                  ],
                  AppButton(
                    label: l10n.signUpButton,
                    busy: _busy,
                    onPressed: _submit,
                  ),
                  const SizedBox(height: 12),
                  Text(
                    l10n.signUpAgeNote,
                    textAlign: TextAlign.center,
                    style: Theme.of(context).textTheme.bodySmall?.copyWith(
                      color: BrandTheme.of(context).muted,
                    ),
                  ),
                  TextButton(
                    onPressed: () => Navigator.of(context).maybePop(),
                    child: Text(l10n.signUpSwitch),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
