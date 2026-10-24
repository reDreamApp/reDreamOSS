import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'package:redream_app/core/api/api_exception.dart';
import 'package:redream_app/core/auth/auth_service.dart';
import 'package:redream_app/core/settings/app_settings.dart';
import 'package:redream_app/core/widgets/app_button.dart';
import 'package:redream_app/core/widgets/feedback.dart';
import 'package:redream_app/features/auth/auth_fields.dart';
import 'package:redream_app/l10n/generated/app_localizations.dart';

/// A personal server (`make start`) has one local user and no sign-in: the
/// app asks for its session right away and moves on once it has one.
class PersonalSignInScreen extends StatefulWidget {
  const PersonalSignInScreen({super.key});

  @override
  State<PersonalSignInScreen> createState() => _PersonalSignInScreenState();
}

class _PersonalSignInScreenState extends State<PersonalSignInScreen> {
  bool _busy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _signIn());
  }

  Future<void> _signIn() async {
    if (_busy || !mounted) return;
    final l10n = AppLocalizations.of(context)!;
    final auth = context.read<AuthService>();
    final settings = context.read<AppSettings>();
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await auth.signInPersonal();
      if (auth.userId case final userId?) {
        if (await settings.notePersonalUser(userId)) {
          await settings.markOnboardingPending(userId);
        }
      }
    } on NetworkException catch (_) {
      if (mounted) setState(() => _error = l10n.errorOffline);
    } on AuthFailure catch (e) {
      if (mounted) {
        setState(
          () => _error = e.kind == AuthFailureKind.personalKey
              ? l10n.personalKeyNeeded
              : l10n.personalSignInFailed,
        );
      }
    } on Exception catch (_) {
      // 403: the server lets in only a browser on its own computer.
      if (mounted) setState(() => _error = l10n.personalSignInFailed);
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
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const AuthTopBar(),
              const AuthHeader(),
              const SizedBox(height: 32),
              if (_error case final error?) ...[
                InlineNotice(message: error),
                const SizedBox(height: 16),
                AppButton(label: l10n.retry, busy: _busy, onPressed: _signIn),
              ] else
                Column(
                  children: [
                    const CircularProgressIndicator(),
                    const SizedBox(height: 16),
                    Text(l10n.personalSigningIn, textAlign: TextAlign.center),
                  ],
                ),
            ],
          ),
        ),
      ),
    );
  }
}
