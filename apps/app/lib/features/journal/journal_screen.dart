import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:redream_app/core/platform/platform_features.dart';
import 'package:redream_app/core/theme/app_theme.dart';
import 'package:redream_app/core/utils/errors.dart';
import 'package:redream_app/core/widgets/app_button.dart';
import 'package:redream_app/core/widgets/feedback.dart';
import 'package:redream_app/features/journal/dream.dart';
import 'package:redream_app/features/journal/dream_card.dart';
import 'package:redream_app/features/journal/dream_editor_screen.dart';
import 'package:redream_app/features/journal/journal_state.dart';
import 'package:redream_app/features/reminders/reminder_tile.dart';
import 'package:redream_app/l10n/generated/app_localizations.dart';

class JournalScreen extends StatefulWidget {
  const JournalScreen({super.key});

  @override
  State<JournalScreen> createState() => _JournalScreenState();
}

class _JournalScreenState extends State<JournalScreen> {
  DreamFilter _filter = DreamFilter.all;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) unawaited(context.read<JournalState>().load());
    });
  }

  Future<void> _refresh() async {
    final l10n = AppLocalizations.of(context)!;
    final journal = context.read<JournalState>();
    await journal.refresh();
    if (!mounted) return;
    if (journal.error case final e?) {
      showAppSnackBar(
        context,
        l10n.journalRefreshFailed(describeError(e, l10n)),
        error: true,
      );
    } else if (journal.usedCache) {
      showAppSnackBar(context, l10n.journalCachedData);
    }
  }

  Future<void> _addDream() => Navigator.push(
    context,
    MaterialPageRoute<Dream>(
      fullscreenDialog: true,
      builder: (_) => const DreamEditorScreen(),
    ),
  );

  Future<void> _showReminders() => showModalBottomSheet<void>(
    context: context,
    showDragHandle: true,
    builder: (_) => const SafeArea(
      child: Padding(
        padding: EdgeInsets.fromLTRB(8, 0, 8, 16),
        child: ReminderTile(),
      ),
    ),
  );

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final theme = Theme.of(context);
    final brand = BrandTheme.of(context);
    final journal = context.watch<JournalState>();
    final all = journal.dreams;
    final shown = journal.filtered(_filter);

    return RefreshIndicator(
      onRefresh: _refresh,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 48),
        children: [
          Text(
            l10n.journalTitle,
            textAlign: TextAlign.center,
            style: theme.textTheme.headlineSmall?.copyWith(
              fontWeight: FontWeight.w700,
            ),
          ),
          const SizedBox(height: 16),
          // Both buttons keep their full labels: on a narrow screen or with
          // long translations the row shrinks as a whole instead of cutting
          // the text off.
          LayoutBuilder(
            builder: (context, constraints) => FittedBox(
              fit: BoxFit.scaleDown,
              child: ConstrainedBox(
                constraints: BoxConstraints(minWidth: constraints.maxWidth),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    AppButton(
                      label: l10n.journalAddDream,
                      icon: Icons.add,
                      expand: false,
                      onPressed: _addDream,
                    ),
                    // No reminders on the web: nothing to set up here.
                    if (PlatformFeatures.reminders) ...[
                      const SizedBox(width: 12),
                      AppButton(
                        label: l10n.journalNotifications,
                        icon: Icons.notifications_none,
                        style: AppButtonStyle.outlined,
                        expand: false,
                        onPressed: _showReminders,
                      ),
                    ],
                  ],
                ),
              ),
            ),
          ),
          const SizedBox(height: 16),
          Wrap(
            spacing: 8,
            children: [
              for (final f in DreamFilter.values)
                FilterChip(
                  label: Text(switch (f) {
                    DreamFilter.all => l10n.filterAll,
                    DreamFilter.dreams => l10n.filterDreams,
                    DreamFilter.nightmares => l10n.filterNightmares,
                  }),
                  selected: _filter == f,
                  onSelected: (_) => setState(() => _filter = f),
                ),
            ],
          ),
          const SizedBox(height: 16),
          if (journal.loading && all.isEmpty)
            const Padding(
              padding: EdgeInsets.all(32),
              child: Center(child: CircularProgressIndicator()),
            )
          else if (journal.error != null && all.isEmpty)
            Padding(
              padding: const EdgeInsets.all(24),
              child: Text(
                l10n.errorLoadFailed,
                textAlign: TextAlign.center,
                style: TextStyle(color: theme.colorScheme.error),
              ),
            )
          else if (shown.isEmpty)
            Padding(
              padding: const EdgeInsets.all(24),
              child: Text(
                l10n.journalEmpty,
                textAlign: TextAlign.center,
                style: theme.textTheme.bodyLarge?.copyWith(color: brand.muted),
              ),
            )
          else
            AnimatedSwitcher(
              duration: const Duration(milliseconds: 300),
              child: Column(
                key: ValueKey(
                  shown.map((d) => '${d.id}-${d.isCurrentDream}').join(','),
                ),
                children: [
                  for (final dream in shown)
                    DreamCard(key: ValueKey(dream.id), dream: dream),
                ],
              ),
            ),
        ],
      ),
    );
  }
}
