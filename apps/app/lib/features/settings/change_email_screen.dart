import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:redream_app/core/auth/auth_service.dart';
import 'package:redream_app/core/utils/errors.dart';
import 'package:redream_app/core/widgets/app_button.dart';
import 'package:redream_app/core/widgets/feedback.dart';
import 'package:redream_app/features/auth/auth_fields.dart';
import 'package:redream_app/features/settings/_form_page.dart';
import 'package:redream_app/l10n/generated/app_localizations.dart';

class ChangeEmailScreen extends StatefulWidget {
  const ChangeEmailScreen({super.key});

  @override
  State<ChangeEmailScreen> createState() => _ChangeEmailScreenState();
}

class _ChangeEmailScreenState extends State<ChangeEmailScreen> {
  final _form = GlobalKey<FormState>();
  late final _email = TextEditingController(
    text: context.read<AuthService>().email ?? '',
  );
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
    if (!_form.currentState!.validate()) return;
    final l10n = AppLocalizations.of(context)!;
    final auth = context.read<AuthService>();
    final newEmail = _email.text.trim();
    if (newEmail.toLowerCase() == (auth.email ?? '').toLowerCase()) {
      setState(() => _error = l10n.changeEmailSame);
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await auth.updateEmail(
        currentPassword: _password.text,
        newEmail: newEmail,
      );
      if (!mounted) return;
      showAppSnackBar(context, l10n.changeEmailSuccess);
      Navigator.pop(context);
    } on Exception catch (e) {
      if (mounted) setState(() => _error = describeError(e, l10n));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return FormPage(
      title: l10n.changeEmailTitle,
      icon: Icons.mail_outline,
      heading: l10n.changeEmailHeading,
      body: l10n.changeEmailBody,
      children: [
        Form(
          key: _form,
          child: Column(
            children: [
              AuthEmailField(
                controller: _email,
                label: l10n.settingsEmail,
                isUsername: false,
              ),
              const SizedBox(height: 12),
              AuthPasswordField(
                controller: _password,
                label: l10n.currentPassword,
                hint: '',
                onSubmitted: (_) => _submit(),
              ),
            ],
          ),
        ),
        const SizedBox(height: 16),
        if (_error != null) ...[
          InlineNotice(message: _error!),
          const SizedBox(height: 16),
        ],
        AppButton(
          label: l10n.changeEmailButton,
          busy: _busy,
          onPressed: _submit,
        ),
      ],
    );
  }
}
