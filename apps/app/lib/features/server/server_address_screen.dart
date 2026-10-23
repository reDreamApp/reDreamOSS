import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'package:redream_app/core/server/server_config.dart';
import 'package:redream_app/core/utils/errors.dart';
import 'package:redream_app/core/widgets/app_button.dart';
import 'package:redream_app/core/widgets/feedback.dart';
import 'package:redream_app/features/auth/auth_fields.dart';
import 'package:redream_app/l10n/generated/app_localizations.dart';

/// Asks for the server's address and checks it with the server itself;
/// once it answers, the server gate moves on to sign-in.
class ServerAddressScreen extends StatefulWidget {
  const ServerAddressScreen({super.key});

  @override
  State<ServerAddressScreen> createState() => _ServerAddressScreenState();
}

class _ServerAddressScreenState extends State<ServerAddressScreen> {
  final _address = TextEditingController();
  bool _busy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    final server = context.read<ServerConfig>();
    _address.text = server.prefill;
    if (server.takeAutoConnect()) {
      WidgetsBinding.instance.addPostFrameCallback((_) => _connect());
    }
  }

  @override
  void dispose() {
    _address.dispose();
    super.dispose();
  }

  Future<void> _connect() async {
    if (_busy || !mounted) return;
    final l10n = AppLocalizations.of(context)!;
    final server = context.read<ServerConfig>();
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await server.connect(_address.text);
    } on ServerFailure catch (e) {
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
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const AuthTopBar(),
              const AuthHeader(),
              const SizedBox(height: 32),
              TextField(
                controller: _address,
                enabled: !_busy,
                autofocus: _address.text.isEmpty,
                keyboardType: TextInputType.url,
                autocorrect: false,
                enableSuggestions: false,
                textInputAction: TextInputAction.go,
                onSubmitted: (_) => _connect(),
                decoration: InputDecoration(
                  labelText: l10n.serverAddressLabel,
                  hintText: l10n.serverAddressHint,
                  helperText: l10n.serverAddressHelp,
                  helperMaxLines: 2,
                  prefixIcon: const Icon(Icons.dns_outlined),
                ),
              ),
              const SizedBox(height: 16),
              if (_error != null) ...[
                InlineNotice(message: _error!),
                const SizedBox(height: 16),
              ],
              AppButton(
                label: l10n.serverConnect,
                busy: _busy,
                onPressed: _connect,
              ),
            ],
          ),
        ),
      ),
    );
  }
}
