import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';
import 'package:redream_app/core/auth/auth_service.dart';
import 'package:redream_app/core/server/server_config.dart';
import 'package:redream_app/core/theme/app_theme.dart';
import 'package:redream_app/core/utils/errors.dart';
import 'package:redream_app/core/widgets/app_button.dart';
import 'package:redream_app/core/widgets/feedback.dart';
import 'package:redream_app/features/auth/auth_fields.dart';
import 'package:redream_app/features/auth/forgot_password_screen.dart';
import 'package:redream_app/features/auth/sign_up_screen.dart';
import 'package:redream_app/features/server/server_gate.dart';
import 'package:redream_app/l10n/generated/app_localizations.dart';

class SignInScreen extends StatefulWidget {
  const SignInScreen({super.key});

  @override
  State<SignInScreen> createState() => _SignInScreenState();
}

class _SignInScreenState extends State<SignInScreen> {
  final _form = GlobalKey<FormState>();
  final _email = TextEditingController();
  final _password = TextEditingController();
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _email.dispose();
    _password.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (_busy || !_form.currentState!.validate()) return;
    final l10n = AppLocalizations.of(context)!;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await context.read<AuthService>().signIn(
        email: _email.text.trim(),
        password: _password.text,
      );
      TextInput.finishAutofillContext();
    } on Exception catch (e) {
      if (mounted) setState(() => _error = describeError(e, l10n));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final server = context.watch<ServerConfig>();
    final closed = server.instance?.registrationClosed ?? false;
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
                  const AuthTopBar(),
                  const AuthHeader(),
                  const SizedBox(height: 32),
                  AuthEmailField(controller: _email, autofocus: true),
                  const SizedBox(height: 12),
                  AuthPasswordField(
                    controller: _password,
                    onSubmitted: (_) => _submit(),
                  ),
                  const SizedBox(height: 16),
                  if (_error != null) ...[
                    InlineNotice(message: _error!),
                    const SizedBox(height: 16),
                  ],
                  AppButton(
                    label: l10n.signInButton,
                    busy: _busy,
                    onPressed: _submit,
                  ),
                  if (!closed)
                    TextButton(
                      onPressed: () => Navigator.push(
                        context,
                        MaterialPageRoute<void>(
                          builder: (_) => const SignUpScreen(),
                        ),
                      ),
                      child: Text(l10n.signInSwitch),
                    ),
                  TextButton(
                    onPressed: () => Navigator.push(
                      context,
                      MaterialPageRoute<void>(
                        builder: (_) => const ForgotPasswordScreen(),
                      ),
                    ),
                    child: Text(l10n.forgotPassword),
                  ),
                  const SizedBox(height: 24),
                  Text(
                    l10n.signInServer(server.address),
                    textAlign: TextAlign.center,
                    style: Theme.of(context).textTheme.bodySmall?.copyWith(
                      color: BrandTheme.of(context).muted,
                    ),
                  ),
                  TextButton(
                    onPressed: _busy ? null : () => changeServer(context),
                    child: Text(l10n.serverChange),
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
