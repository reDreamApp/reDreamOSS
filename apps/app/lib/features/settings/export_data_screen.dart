import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:path_provider/path_provider.dart';
import 'package:provider/provider.dart';
import 'package:redream_app/core/app_services.dart';
import 'package:redream_app/core/widgets/app_button.dart';
import 'package:redream_app/core/widgets/feedback.dart';
import 'package:redream_app/features/settings/_form_page.dart';
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
      final dir = await getApplicationDocumentsDirectory();
      final stamp = DateTime.now().toUtc().toIso8601String().replaceAll(
        ':',
        '-',
      );
      final file = File('${dir.path}/redream_export_$stamp.json');
      await file.writeAsString(
        const JsonEncoder.withIndent('  ').convert(data),
        flush: true,
      );
      if (!mounted) return;
      await showDialog<void>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: Text(l10n.exportReadyTitle),
          content: Text(l10n.exportReadyBody(file.path)),
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
