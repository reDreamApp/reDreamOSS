import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:redream_app/core/auth/auth_service.dart';
import 'package:redream_app/core/utils/errors.dart';
import 'package:redream_app/core/widgets/app_button.dart';
import 'package:redream_app/core/widgets/feedback.dart';
import 'package:redream_app/features/auth/auth_fields.dart';
import 'package:redream_app/features/settings/_form_page.dart';
import 'package:redream_app/l10n/generated/app_localizations.dart';

class ChangePasswordScreen extends StatefulWidget {
  const ChangePasswordScreen({super.key});

  @override
  State<ChangePasswordScreen> createState() => _ChangePasswordScreenState();
}

class _ChangePasswordScreenState extends State<ChangePasswordScreen> {
  final _form = GlobalKey<FormState>();
  final _current = TextEditingController();
  final _next = TextEditingController();
  final _confirm = TextEditingController();
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _current.dispose();
    _next.dispose();
    _confirm.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (!_form.currentState!.validate()) return;
    final l10n = AppLocalizations.of(context)!;
    final auth = context.read<AuthService>();
    if (_next.text == _current.text) {
      setState(() => _error = l10n.changePasswordSame);
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await auth.updatePassword(
        currentPassword: _current.text,
        newPassword: _next.text,
      );
      if (!mounted) return;
      showAppSnackBar(context, l10n.changePasswordSuccess);
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
      title: l10n.changePasswordTitle,
      icon: Icons.lock_outline,
      heading: l10n.changePasswordHeading,
      body: l10n.changePasswordBody,
      children: [
        Form(
          key: _form,
          child: Column(
            children: [
              AuthPasswordField(
                controller: _current,
                label: l10n.currentPassword,
                hint: '',
                textInputAction: TextInputAction.next,
              ),
              const SizedBox(height: 12),
              AuthPasswordField(
                controller: _next,
                label: l10n.newPassword,
                hint: '',
                isNewPassword: true,
                textInputAction: TextInputAction.next,
              ),
              const SizedBox(height: 12),
              AuthPasswordField(
                controller: _confirm,
                label: l10n.confirmNewPassword,
                hint: '',
                isNewPassword: true,
                validator: (v) => validateConfirm(v, _next.text, l10n),
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
          label: l10n.changePasswordButton,
          busy: _busy,
          onPressed: _submit,
        ),
      ],
    );
  }
}
