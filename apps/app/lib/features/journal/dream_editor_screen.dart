import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:redream_app/core/api/api_exception.dart';
import 'package:redream_app/core/settings/app_settings.dart';
import 'package:redream_app/core/utils/errors.dart';
import 'package:redream_app/core/widgets/app_button.dart';
import 'package:redream_app/core/widgets/feedback.dart';
import 'package:redream_app/core/widgets/intensity_slider.dart';
import 'package:redream_app/features/journal/dream.dart';
import 'package:redream_app/features/journal/journal_state.dart';
import 'package:redream_app/l10n/generated/app_localizations.dart';

/// Create (no [existing]) or edit a dream. Pops with the saved [Dream].
class DreamEditorScreen extends StatefulWidget {
  const DreamEditorScreen({super.key, this.existing});

  final Dream? existing;

  @override
  State<DreamEditorScreen> createState() => _DreamEditorScreenState();
}

class _DreamEditorScreenState extends State<DreamEditorScreen> {
  late final _title = TextEditingController(text: widget.existing?.title);
  late final _dream = TextEditingController(
    text: widget.existing?.originalDream,
  );
  late final _rewritten = TextEditingController(
    text: widget.existing?.rewrittenDream,
  );
  late bool _isNightmare = widget.existing?.isNightmare ?? false;
  late int _intensity = widget.existing?.intensity ?? 1;
  bool _saving = false;

  bool get _isEdit => widget.existing != null;

  @override
  void initState() {
    super.initState();
    for (final c in [_title, _dream, _rewritten]) {
      c.addListener(() => setState(() {}));
    }
  }

  @override
  void dispose() {
    _title.dispose();
    _dream.dispose();
    _rewritten.dispose();
    super.dispose();
  }

  bool get _hasChanges {
    final e = widget.existing;
    if (e == null) {
      return _title.text.isNotEmpty ||
          _dream.text.isNotEmpty ||
          _rewritten.text.isNotEmpty;
    }
    return _title.text != e.title ||
        _dream.text != e.originalDream ||
        _isNightmare != e.isNightmare ||
        (_isNightmare && _rewritten.text != (e.rewrittenDream ?? '')) ||
        (_isNightmare && _intensity != (e.intensity ?? 1));
  }

  DreamInput get _input => DreamInput(
    title: _title.text.trim(),
    originalDream: _dream.text.trim(),
    isNightmare: _isNightmare,
    rewrittenDream: _isNightmare && _rewritten.text.trim().isNotEmpty
        ? _rewritten.text.trim()
        : null,
    intensity: _isNightmare ? _intensity : null,
    language: context.read<AppSettings>().language,
  );

  Future<void> _save() async {
    final l10n = AppLocalizations.of(context)!;
    if (_title.text.trim().isEmpty) {
      showAppSnackBar(context, l10n.editorTitleRequired, error: true);
      return;
    }
    if (_dream.text.trim().isEmpty) {
      showAppSnackBar(context, l10n.editorDreamRequired, error: true);
      return;
    }
    setState(() => _saving = true);
    final journal = context.read<JournalState>();
    final input = _input;
    try {
      final result = _isEdit
          ? await journal.update(widget.existing!, input)
          : await journal.create(input);
      if (!mounted) return;
      showAppSnackBar(
        context,
        result.savedOffline
            ? l10n.editorSavedOffline
            : _isEdit
            ? l10n.editorUpdated
            : input.isNightmare
            ? l10n.editorSavedNightmare(input.intensity ?? 0)
            : l10n.editorSavedDream,
      );
      Navigator.pop(context, result.dream);
    } on ApiException catch (e) {
      if (mounted) {
        showAppSnackBar(
          context,
          l10n.editorSaveFailed(describeError(e, l10n)),
          error: true,
        );
      }
    } on Exception catch (e) {
      if (mounted) {
        showAppSnackBar(
          context,
          l10n.editorSaveFailed(describeError(e, l10n)),
          error: true,
        );
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<bool> _confirmLeave() async {
    if (!_hasChanges || _saving) return !_saving;
    final l10n = AppLocalizations.of(context)!;
    return confirmDialog(
      context,
      title: l10n.editorUnsavedTitle,
      body: l10n.editorUnsavedBody,
      confirmLabel: l10n.editorDiscard,
      cancelLabel: l10n.cancel,
      destructive: true,
    );
  }

  Future<void> _close() async {
    if (await _confirmLeave() && mounted) Navigator.pop(context);
  }

  void _clear() => setState(() {
    _title.clear();
    _dream.clear();
    _rewritten.clear();
    _isNightmare = false;
    _intensity = 1;
  });

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final theme = Theme.of(context);
    final help = theme.textTheme.bodySmall?.copyWith(
      color: theme.colorScheme.onSurfaceVariant,
    );

    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, _) async {
        if (!didPop) await _close();
      },
      child: Scaffold(
        appBar: AppBar(
          leading: IconButton(
            icon: const Icon(Icons.close),
            onPressed: _close,
          ),
          title: Text(_isEdit ? l10n.editorTitleEdit : l10n.editorTitleNew),
          actions: [
            if (!_isEdit && _hasChanges)
              TextButton(
                onPressed: _clear,
                style: TextButton.styleFrom(
                  foregroundColor: theme.colorScheme.error,
                ),
                child: Text(l10n.editorClear),
              ),
          ],
        ),
        body: SafeArea(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(16, 16, 16, 32),
            children: [
              Text(l10n.editorTitleHelp, style: help),
              const SizedBox(height: 8),
              TextField(
                controller: _title,
                maxLines: 3,
                minLines: 1,
                textCapitalization: TextCapitalization.sentences,
                decoration: InputDecoration(
                  labelText: l10n.editorTitleLabel,
                  hintText: l10n.editorTitleHint,
                ),
              ),
              const SizedBox(height: 20),
              Text(l10n.editorDreamHelp, style: help),
              const SizedBox(height: 8),
              TextField(
                controller: _dream,
                minLines: 4,
                maxLines: null,
                textCapitalization: TextCapitalization.sentences,
                decoration: InputDecoration(
                  labelText: l10n.editorDreamLabel,
                  hintText: l10n.editorDreamHint,
                  alignLabelWithHint: true,
                ),
              ),
              const SizedBox(height: 12),
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                title: Text(l10n.editorIsNightmare),
                subtitle: Text(l10n.editorIsNightmareHelp),
                value: _isNightmare,
                onChanged: (v) => setState(() {
                  _isNightmare = v;
                  if (!v) _rewritten.clear();
                }),
              ),
              if (_isNightmare) ...[
                const SizedBox(height: 12),
                Text(l10n.editorIntensityHelp, style: help),
                const SizedBox(height: 8),
                Container(
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    border: Border.all(color: theme.colorScheme.outline),
                    borderRadius: BorderRadius.circular(14),
                  ),
                  child: IntensitySlider(
                    title: l10n.editorIntensityTitle,
                    value: _intensity,
                    onChanged: (v) => setState(() => _intensity = v),
                  ),
                ),
                const SizedBox(height: 20),
                Text(l10n.editorRewrittenHelp, style: help),
                const SizedBox(height: 8),
                TextField(
                  controller: _rewritten,
                  minLines: 4,
                  maxLines: null,
                  textCapitalization: TextCapitalization.sentences,
                  decoration: InputDecoration(
                    labelText: l10n.editorRewrittenLabel,
                    hintText: l10n.editorRewrittenHint,
                    alignLabelWithHint: true,
                  ),
                ),
              ],
              const SizedBox(height: 28),
              AppButton(
                label: _isEdit ? l10n.editorSaveChanges : l10n.editorSave,
                style: AppButtonStyle.gradient,
                busy: _saving,
                onPressed: _save,
              ),
            ],
          ),
        ),
      ),
    );
  }
}
