import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';
import 'package:redream_app/core/server/server_config.dart';
import 'package:redream_app/core/theme/app_theme.dart';
import 'package:redream_app/core/widgets/app_button.dart';
import 'package:redream_app/core/widgets/app_card.dart';
import 'package:redream_app/core/widgets/assistant_sheep.dart';
import 'package:redream_app/core/widgets/intensity_slider.dart';
import 'package:redream_app/features/chat/chat_actions.dart';
import 'package:redream_app/features/chat/chat_errors.dart';
import 'package:redream_app/features/chat/chat_state.dart';
import 'package:redream_app/features/chat/message_bubble.dart';
import 'package:redream_app/features/chat/stage_indicator.dart';
import 'package:redream_app/features/journal/journal_state.dart';
import 'package:redream_app/features/shell/shell_tabs.dart';
import 'package:redream_app/l10n/generated/app_localizations.dart';
import 'package:url_launcher/url_launcher.dart';

class ChatScreen extends StatefulWidget {
  const ChatScreen({super.key});

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> {
  final _input = TextEditingController();
  final _scroll = ScrollController();
  int _lastCount = 0;
  int _lastLength = 0;

  // Voice notes work as in common messengers. A tap records hands-free
  // until Send or Discard. Holding records until the finger lifts, which
  // sends; sliding left while holding discards, sliding up goes hands-free.
  static const _slideDistance = 72.0;

  /// A shorter press counts as a tap, so a slip of the finger sends nothing.
  static const _holdToSend = Duration(milliseconds: 800);

  bool _micHeld = false;
  Offset _micDownAt = Offset.zero;
  Duration _micDownTime = Duration.zero;

  /// Runs from the start of a recording; the counter in the composer.
  final _recordingTime = Stopwatch();

  @override
  void initState() {
    super.initState();
    _input.addListener(() => setState(() {}));
  }

  @override
  void dispose() {
    _input.dispose();
    _scroll.dispose();
    super.dispose();
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scroll.hasClients) return;
      unawaited(
        _scroll.animateTo(
          _scroll.position.maxScrollExtent,
          duration: const Duration(milliseconds: 250),
          curve: Curves.easeOut,
        ),
      );
    });
  }

  Future<void> _send() async {
    final text = _input.text.trim();
    if (text.isEmpty) return;
    _input.clear();
    await context.read<ChatState>().send(text);
  }

  void _micDown(PointerDownEvent event, ChatState state) {
    if (_micHeld || state.recording) return;
    setState(() {
      _micHeld = true;
      _micDownAt = event.position;
      _micDownTime = event.timeStamp;
    });
    unawaited(HapticFeedback.selectionClick());
    // If the finger is gone by the time recording runs (a tap, or the
    // permission prompt took over), it simply continues hands-free.
    _startRecording(state);
  }

  void _startRecording(ChatState state) {
    _recordingTime
      ..reset()
      ..start();
    unawaited(state.startRecording());
  }

  void _micMove(PointerMoveEvent event, ChatState state) {
    if (!_micHeld || !state.recording) return;
    final slid = event.position - _micDownAt;
    if (slid.dx <= -_slideDistance) {
      setState(() => _micHeld = false);
      unawaited(HapticFeedback.selectionClick());
      unawaited(state.cancelRecording());
    } else if (slid.dy <= -_slideDistance) {
      unawaited(HapticFeedback.selectionClick());
      setState(() => _micHeld = false);
    }
  }

  void _micUp(PointerUpEvent event, ChatState state) {
    if (!_micHeld) return;
    final held = event.timeStamp - _micDownTime;
    setState(() => _micHeld = false);
    if (state.recording && held >= _holdToSend) {
      unawaited(_sendRecording(state));
    }
  }

  void _micLost() {
    if (_micHeld) setState(() => _micHeld = false);
  }

  /// Stops the recording and sends what was said as a message, together
  /// with anything already typed.
  Future<void> _sendRecording(ChatState state) async {
    final chatId = state.chatId;
    final transcript = await state.stopRecording();
    if (transcript == null || !mounted) return;
    final typed = _input.text.trim();
    final text = typed.isEmpty ? transcript : '$typed $transcript';
    if (state.chatId != chatId) {
      // Another chat was opened meanwhile: leave the words for review
      // instead of sending them into the wrong session.
      _input.text = text;
      _input.selection = TextSelection.collapsed(offset: text.length);
      return;
    }
    _input.clear();
    await state.send(text);
  }

  Future<void> _openJournal() async {
    try {
      await context.read<JournalState>().refresh();
    } on Exception catch (_) {}
    if (mounted) context.read<ShellTabs>().select(ShellTab.journal);
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final theme = Theme.of(context);
    final scheme = theme.colorScheme;
    final state = context.watch<ChatState>();

    final count = state.messages.length + (state.awaitingReply ? 1 : 0);
    final length = state.messages.isEmpty
        ? 0
        : state.messages.last.content.length;
    if (count != _lastCount || length != _lastLength) {
      _lastCount = count;
      _lastLength = length;
      _scrollToBottom();
    }

    final safety = state.safetyEvent;
    final error = state.errorMessage;

    return Stack(
      children: [
        Column(
          children: [
            const _Header(),
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 4),
              child: StageIndicator(current: state.stage),
            ),
            const Divider(),
            Expanded(
              child: GestureDetector(
                onTap: FocusScope.of(context).unfocus,
                child: RefreshIndicator(
                  onRefresh: context.refreshChat,
                  child: ListView.builder(
                    controller: _scroll,
                    physics: const AlwaysScrollableScrollPhysics(),
                    padding: const EdgeInsets.fromLTRB(12, 8, 12, 8),
                    itemCount: count,
                    itemBuilder: (_, i) => i < state.messages.length
                        ? MessageBubble(message: state.messages[i])
                        : const _ThinkingBubble(),
                  ),
                ),
              ),
            ),
            if (safety != null)
              _Banner(
                title: l10n.safetyHelplineTitle,
                body: safety.message ?? l10n.safetyHelplineBody,
                background: scheme.errorContainer,
                foreground: scheme.onErrorContainer,
                actionLabel: l10n.safetyHelplineButton,
                onAction: () => launchUrl(
                  Uri.parse(l10n.safetyHelplineUrl),
                  mode: LaunchMode.externalApplication,
                ),
                onDismiss: state.dismissSafetyEvent,
              ),
            if (state.retryAfterSeconds != null)
              _Banner(
                title: l10n.dailyLimitTitle,
                body: l10n.dailyLimitBody,
                background: scheme.tertiaryContainer,
                foreground: scheme.onTertiaryContainer,
                onDismiss: state.dismissError,
              ),
            if (state.startFailed)
              _Banner(
                body: l10n.chatStartErrorBody,
                background: scheme.errorContainer,
                foreground: scheme.onErrorContainer,
                actionLabel: l10n.retry,
                onAction: context.resumeOrStartChat,
              )
            else if (error != null && state.retryAfterSeconds == null)
              _Banner(
                body: chatErrorText(error, l10n),
                background: scheme.errorContainer,
                foreground: scheme.onErrorContainer,
                onDismiss: state.dismissError,
              ),
            if (state.micPermissionDenied)
              _Banner(
                body: l10n.chatMicPermissionDenied,
                background: scheme.tertiaryContainer,
                foreground: scheme.onTertiaryContainer,
                onDismiss: state.dismissError,
              ),
            if (state.failedMessageId != null && !state.sending)
              _Banner(
                title: l10n.chatNotDelivered,
                body: l10n.chatResendHint,
                background: scheme.errorContainer,
                foreground: scheme.onErrorContainer,
                actionLabel: l10n.chatResend,
                onAction: state.retryFailedSend,
              ),
            const Divider(),
            SafeArea(
              top: false,
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 10, 16, 12),
                child: state.isDone
                    ? _FinishedPanel(state: state, onOpenJournal: _openJournal)
                    : _composer(context, state, l10n),
              ),
            ),
          ],
        ),
        if (state.loadingChat)
          Positioned.fill(
            child: ColoredBox(
              color: scheme.surface.withValues(alpha: 0.85),
              child: Center(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    const CircularProgressIndicator(),
                    const SizedBox(height: 12),
                    Text(l10n.chatLoading),
                  ],
                ),
              ),
            ),
          ),
      ],
    );
  }

  Widget _composer(
    BuildContext context,
    ChatState state,
    AppLocalizations l10n,
  ) {
    final scheme = Theme.of(context).colorScheme;
    final ready = state.chatId != null && !state.sending && !state.loadingChat;
    final busyMic = state.recording || state.transcribing;
    // Live: the server can turn speech on or off while the app runs. A
    // recording already under way keeps its button.
    final serverVoice =
        busyMic ||
        context.select<ServerConfig, bool>(
          (s) => s.instance?.transcription ?? false,
        );
    final canSend = ready && !busyMic && _input.text.trim().isNotEmpty;

    final holding = state.recording && _micHeld;
    final handsFree = state.recording && !_micHeld;
    final muted = BrandTheme.of(context).muted;
    final dot = Icon(Icons.fiber_manual_record, color: scheme.error, size: 18);
    // Each chat keeps one of the hints, like its greeting.
    final hints = [
      l10n.chatInputHint,
      l10n.chatInputHint2,
      l10n.chatInputHint3,
    ];
    final hint = hints[(state.chatId ?? 0) % hints.length];

    // One row for every state, so the buttons never change place: the mic
    // turns into Discard, the send button sends text or the recording.
    return Row(
      crossAxisAlignment: CrossAxisAlignment.end,
      children: [
        Expanded(
          // The field stays mounted so it keeps its focus while recording.
          child: IndexedStack(
            index: holding ? 1 : (handsFree ? 2 : 0),
            alignment: Alignment.centerLeft,
            children: [
              TextField(
                controller: _input,
                minLines: 1,
                maxLines: 6,
                enabled: ready && !busyMic,
                textCapitalization: TextCapitalization.sentences,
                decoration: InputDecoration(
                  hintText: state.transcribing ? l10n.chatTranscribing : hint,
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(999),
                    borderSide: BorderSide.none,
                  ),
                  contentPadding: const EdgeInsets.symmetric(
                    horizontal: 20,
                    vertical: 14,
                  ),
                ),
                onSubmitted: canSend ? (_) => _send() : null,
              ),
              if (holding)
                Row(
                  children: [
                    dot,
                    const SizedBox(width: 8),
                    _Elapsed(_recordingTime),
                    const SizedBox(width: 12),
                    Icon(Icons.chevron_left_rounded, color: muted),
                    Expanded(
                      child: Text(
                        l10n.chatMicSlideDiscard,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(color: muted),
                      ),
                    ),
                    Icon(Icons.keyboard_arrow_up_rounded, color: muted),
                    Icon(Icons.lock_outline_rounded, size: 18, color: muted),
                  ],
                )
              else
                const SizedBox.shrink(),
              if (handsFree)
                Row(
                  children: [
                    dot,
                    const SizedBox(width: 8),
                    Flexible(
                      child: Text(
                        l10n.chatMicRecording,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                    const SizedBox(width: 8),
                    _Elapsed(_recordingTime),
                  ],
                )
              else
                const SizedBox.shrink(),
            ],
          ),
        ),
        if (state.voiceInputAvailable && serverVoice) ...[
          const SizedBox(width: 8),
          SizedBox(
            key: const ValueKey('mic'),
            width: 52,
            height: 52,
            child: state.transcribing
                ? const Padding(
                    padding: EdgeInsets.all(14),
                    child: CircularProgressIndicator(strokeWidth: 2.5),
                  )
                : handsFree
                ? IconButton.filled(
                    tooltip: l10n.chatMicCancel,
                    onPressed: state.cancelRecording,
                    style: IconButton.styleFrom(
                      backgroundColor: scheme.error,
                      foregroundColor: scheme.onError,
                    ),
                    // The stop square with a cross through it.
                    icon: const Icon(Icons.disabled_by_default_rounded),
                  )
                : Listener(
                    behavior: HitTestBehavior.opaque,
                    onPointerDown: ready ? (e) => _micDown(e, state) : null,
                    onPointerMove: (e) => _micMove(e, state),
                    onPointerUp: (e) => _micUp(e, state),
                    onPointerCancel: (_) => _micLost(),
                    child: Semantics(
                      button: true,
                      enabled: ready,
                      label: l10n.chatMicRecord,
                      // Assistive taps record hands-free.
                      onTap: ready ? () => _startRecording(state) : null,
                      child: AnimatedScale(
                        scale: holding ? 1.2 : 1,
                        duration: const Duration(milliseconds: 120),
                        child: AnimatedContainer(
                          duration: const Duration(milliseconds: 120),
                          decoration: BoxDecoration(
                            shape: BoxShape.circle,
                            color: _micHeld ? scheme.error : Colors.transparent,
                          ),
                          child: Icon(
                            _micHeld
                                ? Icons.mic_rounded
                                : Icons.mic_none_rounded,
                            color: _micHeld
                                ? scheme.onError
                                : (ready
                                      ? scheme.onSurfaceVariant
                                      : scheme.onSurface.withValues(
                                          alpha: 0.38,
                                        )),
                          ),
                        ),
                      ),
                    ),
                  ),
          ),
        ],
        const SizedBox(width: 8),
        SizedBox(
          width: 52,
          height: 52,
          child: handsFree
              ? IconButton.filled(
                  tooltip: l10n.chatMicStop,
                  onPressed: () => _sendRecording(state),
                  icon: const Icon(Icons.send_rounded),
                )
              : IconButton.filled(
                  tooltip: l10n.chatSend,
                  onPressed: canSend ? _send : null,
                  style: IconButton.styleFrom(
                    backgroundColor: scheme.surfaceContainerHighest,
                    foregroundColor: scheme.onSurface,
                    disabledBackgroundColor: scheme.surfaceContainerHighest,
                    disabledForegroundColor: scheme.onSurfaceVariant,
                  ),
                  icon: const Icon(Icons.send_rounded),
                ),
        ),
      ],
    );
  }
}

/// Minutes and seconds of a running [Stopwatch], refreshed every second.
class _Elapsed extends StatefulWidget {
  const _Elapsed(this.watch);

  final Stopwatch watch;

  @override
  State<_Elapsed> createState() => _ElapsedState();
}

class _ElapsedState extends State<_Elapsed> {
  late final Timer _tick;

  @override
  void initState() {
    super.initState();
    _tick = Timer.periodic(const Duration(seconds: 1), (_) => setState(() {}));
  }

  @override
  void dispose() {
    _tick.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final seconds = widget.watch.elapsed.inSeconds;
    return Text(
      '${seconds ~/ 60}:${(seconds % 60).toString().padLeft(2, '0')}',
      style: const TextStyle(fontFeatures: [FontFeature.tabularFigures()]),
    );
  }
}

/// Shown once the session is finished: the 0-10 rating (only right after a
/// live session, until saved), the way to the journal and a fresh start.
class _FinishedPanel extends StatelessWidget {
  const _FinishedPanel({required this.state, required this.onOpenJournal});

  final ChatState state;
  final VoidCallback onOpenJournal;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (!state.resumedDone && !state.intensitySubmitted) ...[
          _IntensityCard(
            saving: state.intensitySaving,
            onSubmit: state.submitIntensity,
          ),
          const SizedBox(height: 10),
        ],
        if (state.resumedDone)
          Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: Text(
              l10n.chatFinishedReadOnly,
              textAlign: TextAlign.center,
              style: TextStyle(color: scheme.onSurfaceVariant, fontSize: 13),
            ),
          ),
        AppCard(
          highlight: true,
          padding: EdgeInsets.zero,
          onTap: onOpenJournal,
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: 14, horizontal: 16),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Icon(Icons.favorite, color: scheme.primary),
                const SizedBox(width: 10),
                Flexible(
                  child: Text(
                    l10n.chatSessionDoneCta,
                    style: const TextStyle(
                      fontSize: 16,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
        TextButton.icon(
          onPressed: context.startNewChat,
          icon: const Icon(Icons.add),
          label: Text(l10n.chatStartNewSession),
        ),
      ],
    );
  }
}

class _IntensityCard extends StatefulWidget {
  const _IntensityCard({required this.saving, required this.onSubmit});

  final bool saving;
  final ValueChanged<int> onSubmit;

  @override
  State<_IntensityCard> createState() => _IntensityCardState();
}

class _IntensityCardState extends State<_IntensityCard> {
  int _value = 5;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return AppCard(
      child: Column(
        children: [
          IntensitySlider(
            value: _value,
            title: l10n.chatIntensityPrompt,
            onChanged: (v) => setState(() => _value = v),
          ),
          const SizedBox(height: 12),
          AppButton(
            label: l10n.submit,
            icon: Icons.check,
            busy: widget.saving,
            onPressed: widget.saving ? null : () => widget.onSubmit(_value),
          ),
        ],
      ),
    );
  }
}

/// The assistant's sheep and its name above the progress bar.
class _Header extends StatelessWidget {
  const _Header();

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
      child: Row(
        children: [
          const AssistantSheep(),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              l10n.chatAssistantName,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w700),
            ),
          ),
        ],
      ),
    );
  }
}

class _ThinkingBubble extends StatefulWidget {
  const _ThinkingBubble();

  @override
  State<_ThinkingBubble> createState() => _ThinkingBubbleState();
}

class _ThinkingBubbleState extends State<_ThinkingBubble>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1200),
  )..repeat();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final brand = BrandTheme.of(context);
    final scheme = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        children: [
          const SizedBox(width: 44),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
            decoration: BoxDecoration(
              color: brand.assistantBubble,
              borderRadius: BorderRadius.circular(18),
            ),
            child: AnimatedBuilder(
              animation: _controller,
              builder: (_, _) => Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  for (var i = 0; i < 3; i++) ...[
                    if (i > 0) const SizedBox(width: 5),
                    _dot(i, scheme.onSurfaceVariant),
                  ],
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _dot(int i, Color color) {
    final phase = ((_controller.value * 3) - i).clamp(0.0, 1.0);
    final alpha = 0.3 + 0.7 * (1 - (phase * 2 - 1).abs());
    return Container(
      width: 8,
      height: 8,
      decoration: BoxDecoration(
        color: color.withValues(alpha: alpha),
        shape: BoxShape.circle,
      ),
    );
  }
}

class _Banner extends StatelessWidget {
  const _Banner({
    required this.body,
    required this.background,
    required this.foreground,
    this.title,
    this.actionLabel,
    this.onAction,
    this.onDismiss,
  });

  final String? title;
  final String body;
  final Color background;
  final Color foreground;
  final String? actionLabel;
  final VoidCallback? onAction;
  final VoidCallback? onDismiss;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      color: background,
      padding: const EdgeInsets.fromLTRB(16, 10, 8, 10),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                if (title != null)
                  Text(
                    title!,
                    style: TextStyle(
                      fontWeight: FontWeight.w600,
                      color: foreground,
                    ),
                  ),
                Text(body, style: TextStyle(color: foreground, fontSize: 13)),
              ],
            ),
          ),
          if (actionLabel != null)
            TextButton(
              onPressed: onAction,
              style: TextButton.styleFrom(foregroundColor: foreground),
              child: Text(actionLabel!),
            ),
          if (onDismiss != null)
            IconButton(
              onPressed: onDismiss,
              icon: Icon(Icons.close, size: 18, color: foreground),
            ),
        ],
      ),
    );
  }
}
