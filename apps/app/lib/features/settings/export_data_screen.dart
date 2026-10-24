import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:redream_app/core/app_services.dart';
import 'package:redream_app/core/widgets/app_button.dart';
import 'package:redream_app/core/widgets/feedback.dart';
import 'package:redream_app/features/settings/_form_page.dart';
import 'package:redream_app/features/settings/export_file.dart'
    if (dart.library.js_interop) 'package:redream_app/features/settings/export_file_web.dart';
import 'package:redream_app/l10n/generated/app_localizations.dart';

class ExportDataScreen extends StatefulWidget {
  const ExportDataScreen({super.key});

  @override
  State<ExportDataScreen> createState() => _ExportDataScreenState();
}

class _ExportDataScreenState extends State<ExportDataScreen> {
  bool _busy = false;
  String? _error;

  Future<void> _export() async {
    final l10n = AppLocalizations.of(context)!;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final data = await context.read<AppServices>().account.exportData();
      final stamp = DateTime.now().toUtc().toIso8601String().replaceAll(
        ':',
        '-',
      );
      // A file in the app's documents; in a browser, a download.
      final saved = await saveExport(
        'redream_export_$stamp.json',
        const JsonEncoder.withIndent('  ').convert(data),
      );
      if (!mounted) return;
      await showDialog<void>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: Text(l10n.exportReadyTitle),
          content: Text(l10n.exportReadyBody(saved)),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(ctx),
              child: Text(l10n.ok),
            ),
          ],
        ),
      );
    } on Exception catch (_) {
      if (mounted) setState(() => _error = l10n.exportFailed);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return FormPage(
      title: l10n.exportTitle,
      icon: Icons.download_outlined,
      heading: l10n.exportTitle,
      body: l10n.exportBody,
      children: [
        if (_error != null) ...[
          InlineNotice(message: _error!),
          const SizedBox(height: 16),
        ],
        AppButton(
          label: l10n.exportButton,
          icon: Icons.download_outlined,
          busy: _busy,
          onPressed: _export,
        ),
      ],
    );
  }
}
