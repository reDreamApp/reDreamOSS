import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:redream_app/core/server/server_config.dart';
import 'package:redream_app/core/theme/app_theme.dart';
import 'package:redream_app/core/utils/errors.dart';
import 'package:redream_app/core/widgets/app_button.dart';
import 'package:redream_app/core/widgets/app_card.dart';
import 'package:redream_app/core/widgets/feedback.dart';
import 'package:redream_app/features/chat/chat_actions.dart';
import 'package:redream_app/features/chat/chat_errors.dart';
import 'package:redream_app/features/chat/chat_state.dart';
import 'package:redream_app/features/journal/dream.dart';
import 'package:redream_app/features/journal/dream_card.dart';
import 'package:redream_app/features/journal/journal_state.dart';
import 'package:redream_app/features/journal/practice_audio_screen.dart';
import 'package:redream_app/features/journal/practice_helpers.dart';
import 'package:redream_app/features/shell/shell_tabs.dart';
import 'package:redream_app/l10n/generated/app_localizations.dart';

/// How long the rewritten dream has to stay open to count as a rehearsal:
/// the time to read it calmly and picture it, between half a minute and two.
int rehearsalSeconds(String rewrittenDream) {
  final words = rewrittenDream.trim().split(RegExp(r'\s+')).length;
  return (words * 0.75).round().clamp(30, 120);
}

/// One dream in full. For a rewritten dream this is also the reading
/// rehearsal: staying on the new version long enough records a practice.
class DreamDetailScreen extends StatefulWidget {
  const DreamDetailScreen({required this.dream, super.key});

  final Dream dream;

  @override
  State<DreamDetailScreen> createState() => _DreamDetailScreenState();
}

class _DreamDetailScreenState extends State<DreamDetailScreen> {
  final _controller = PageController();
  int _page = 0;
  bool _openingChat = false;

  /// Seconds the rewritten dream has been in view; the reading rehearsal.
  int _readSeconds = 0;
  bool _recording = false;
  bool _recordFailed = false;
  late final Timer _tick;

  /// Which meditation line this visit shows: the next one after every
  /// rehearsal, and the same one for as long as the screen stays open.
  late final int _meditation;

  @override
  void initState() {
    super.initState();
    _meditation = widget.dream.practiceCount;
    _tick = Timer.periodic(const Duration(seconds: 1), (_) => _onTick());
  }

  @override
  void dispose() {
    _tick.cancel();
    _controller.dispose();
    super.dispose();
  }

  void _onTick() {
    if (!mounted || _recording || _recordFailed) return;
    final dream =
        context.read<JournalState>().byId(widget.dream.id) ?? widget.dream;
    final rewritten = dream.rewrittenDream;
    // Only time spent on the new version counts: this screen in front, the
    // app open, and not once today's practice is recorded.
    final lifecycle = WidgetsBinding.instance.lifecycleState;
    if (rewritten == null ||
        !dream.canPractice ||
        _page != 0 ||
        dream.practicedOn(DateTime.now()) ||
        !(ModalRoute.of(context)?.isCurrent ?? false) ||
        (lifecycle != null && lifecycle != AppLifecycleState.resumed)) {
      return;
    }
    setState(() => _readSeconds++);
    if (_readSeconds >= rehearsalSeconds(rewritten)) {
      unawaited(_recordReading(dream));
    }
  }

  Future<void> _recordReading(Dream dream) async {
    final l10n = AppLocalizations.of(context)!;
    final journal = context.read<JournalState>();
    setState(() => _recording = true);
    try {
      final result = await journal.recordPractice(dream);
      if (!mounted) return;
      if (!result.alreadyToday) {
        showAppSnackBar(
          context,
          result.savedOffline
              ? l10n.practiceSavedOffline
              : l10n.practiceRecorded,
          duration: const Duration(seconds: 4),
        );
      }
    } on Exception catch (e) {
      if (!mounted) return;
      // Stop counting until the user asks to try again.
      setState(() => _recordFailed = true);
      showAppSnackBar(
        context,
        l10n.practiceSaveFailed(describeError(e, l10n)),
        error: true,
        duration: const Duration(seconds: 5),
        action: SnackBarAction(
          label: l10n.retry,
          textColor: Colors.white,
          onPressed: () => setState(() => _recordFailed = false),
        ),
      );
    } finally {
      if (mounted) setState(() => _recording = false);
    }
  }

  Future<void> _openChatHistory(Dream dream) async {
    final l10n = AppLocalizations.of(context)!;
    final chatId = dream.chatId;
    if (chatId == null) {
      showAppSnackBar(context, l10n.detailNoChatHistory);
      return;
    }
    setState(() => _openingChat = true);
    final chat = context.read<ChatState>();
    final tabs = context.read<ShellTabs>();
    try {
      await chat.loadChats();
      if (!mounted) return;
      final opened = await context.openChat(chatId);
      if (!mounted) return;
      if (!opened) {
        final message = chat.errorMessage;
        showAppSnackBar(
          context,
          message == null ? l10n.errorUnknown : chatErrorText(message, l10n),
          error: true,
        );
        return;
      }
      tabs.select(ShellTab.session);
      Navigator.popUntil(context, (r) => r.isFirst);
    } finally {
      if (mounted) setState(() => _openingChat = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final theme = Theme.of(context);
    final brand = BrandTheme.of(context);
    final dream =
        context.watch<JournalState>().byId(widget.dream.id) ?? widget.dream;
    final twoSided = dream.isNightmare && dream.hasRewrite;
    // Audio made before the server turned speech off still plays.
    final rehearsalAudio = context.select<ServerConfig, bool>(
      (s) => s.instance?.rehearsalAudio ?? false,
    );
    final canListen =
        dream.canPractice &&
        (rehearsalAudio || (dream.rehearsalAudioUrl ?? '').isNotEmpty);
    final pages = twoSided
        ? [
            (l10n.detailRewritten, dream.rewrittenDream!),
            (l10n.detailOriginal, dream.originalDream),
          ]
        : [(l10n.detailDream, dream.originalDream)];
    final showsOriginal = !twoSided || _page == 1;
    final originalTint = dream.isNightmare && showsOriginal
        ? dreamTint(context, nightmare: true)
        : null;

    return Scaffold(
      appBar: AppBar(
        title: Text(dream.title, maxLines: 1, overflow: TextOverflow.ellipsis),
      ),
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            children: [
              Expanded(
                child: AppCard(
                  padding: EdgeInsets.zero,
                  highlight: _page == 0 && twoSided,
                  // The original of a nightmare wears the journal's
                  // nightmare colours.
                  gradient: originalTint?.gradient,
                  borderColor: originalTint?.border,
                  child: Column(
                    children: [
                      Expanded(
                        child: PageView.builder(
                          controller: _controller,
                          itemCount: pages.length,
                          onPageChanged: (i) => setState(() => _page = i),
                          itemBuilder: (_, i) => LayoutBuilder(
                            builder: (context, box) => SingleChildScrollView(
                              padding: const EdgeInsets.all(20),
                              // At least as tall as the card, so a short
                              // dream leaves the lower part to the
                              // meditation prompt.
                              child: ConstrainedBox(
                                constraints: BoxConstraints(
                                  minHeight: box.maxHeight - 40,
                                ),
                                child: Column(
                                  crossAxisAlignment:
                                      CrossAxisAlignment.stretch,
                                  mainAxisAlignment:
                                      MainAxisAlignment.spaceBetween,
                                  children: [
                                    Column(
                                      crossAxisAlignment:
                                          CrossAxisAlignment.start,
                                      children: [
                                        Text(
                                          pages[i].$1,
                                          style: theme.textTheme.headlineSmall
                                              ?.copyWith(
                                                fontWeight: FontWeight.w700,
                                              ),
                                        ),
                                        const SizedBox(height: 12),
                                        Text(
                                          pages[i].$2,
                                          style: theme.textTheme.bodyLarge
                                              ?.copyWith(height: 1.5),
                                        ),
                                      ],
                                    ),
                                    if (twoSided && i == 0)
                                      _Meditation(variant: _meditation),
                                  ],
                                ),
                              ),
                            ),
                          ),
                        ),
                      ),
                      if (twoSided)
                        Padding(
                          padding: const EdgeInsets.only(bottom: 14),
                          child: PageDots(count: pages.length, index: _page),
                        ),
                    ],
                  ),
                ),
              ),
              if (twoSided) ...[
                const SizedBox(height: 8),
                Text(
                  l10n.detailSwipeHint,
                  style: theme.textTheme.bodySmall?.copyWith(
                    color: brand.muted,
                  ),
                ),
              ],
              if (dream.canPractice) ...[
                const SizedBox(height: 16),
                _ReadingRehearsal(
                  done: dream.practicedOn(DateTime.now()),
                  progress:
                      _readSeconds / rehearsalSeconds(dream.rewrittenDream!),
                ),
              ],
              if (canListen || dream.chatId != null) ...[
                const SizedBox(height: 16),
                Row(
                  children: [
                    if (dream.chatId != null)
                      Expanded(
                        child: AppButton(
                          label: l10n.detailChatHistory,
                          icon: Icons.chat_bubble_outline,
                          style: AppButtonStyle.outlined,
                          busy: _openingChat,
                          onPressed: () => _openChatHistory(dream),
                        ),
                      ),
                    if (dream.chatId != null && canListen)
                      const SizedBox(width: 12),
                    if (canListen)
                      Expanded(
                        child: AppButton(
                          label: l10n.detailListen,
                          icon: Icons.graphic_eq_rounded,
                          style: AppButtonStyle.gradient,
                          onPressed: () => Navigator.push(
                            context,
                            MaterialPageRoute<void>(
                              builder: (_) => PracticeAudioScreen(dream: dream),
                            ),
                          ),
                        ),
                      ),
                  ],
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

/// The reading rehearsal under the dream: one quiet line and a bar that
/// fills while the new version is in view and ends in green.
class _ReadingRehearsal extends StatelessWidget {
  const _ReadingRehearsal({required this.done, required this.progress});

  final bool done;
  final double progress;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final theme = Theme.of(context);
    final brand = BrandTheme.of(context);
    return Column(
      children: [
        Text(
          done ? l10n.detailRehearsalDone : l10n.detailRehearsalHint,
          textAlign: TextAlign.center,
          style: theme.textTheme.bodyMedium?.copyWith(color: brand.muted),
        ),
        const SizedBox(height: 10),
        // The count moves once a second; the bar glides from step to step.
        TweenAnimationBuilder<double>(
          tween: Tween(end: done ? 1 : progress.clamp(0, 1)),
          duration: const Duration(seconds: 1),
          builder: (context, value, _) => ClipRRect(
            borderRadius: BorderRadius.circular(999),
            child: LinearProgressIndicator(
              minHeight: 6,
              value: value,
              backgroundColor: brand.cardBorder,
              color: Color.lerp(
                BrandColors.turquoise,
                BrandColors.green,
                Curves.easeIn.transform(value.clamp(0, 1)),
              ),
            ),
          ),
        ),
      ],
    );
  }
}

/// The quiet moment after reading, at the foot of the rewritten dream.
class _Meditation extends StatelessWidget {
  const _Meditation({required this.variant});

  /// Picks one of the lines; any number works.
  final int variant;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final theme = Theme.of(context);
    final lines = [
      l10n.detailMeditation,
      l10n.detailMeditation2,
      l10n.detailMeditation3,
      l10n.detailMeditation4,
    ];
    return Padding(
      padding: const EdgeInsets.only(top: 28, bottom: 8),
      child: Column(
        children: [
          Icon(
            Icons.self_improvement_rounded,
            size: 56,
            color: theme.colorScheme.primary.withValues(alpha: 0.55),
          ),
          const SizedBox(height: 8),
          Text(
            lines[variant % lines.length],
            textAlign: TextAlign.center,
            style: theme.textTheme.bodyMedium?.copyWith(
              color: BrandTheme.of(context).muted,
            ),
          ),
        ],
      ),
    );
  }
}
