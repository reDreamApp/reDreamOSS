import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:just_audio/just_audio.dart';
import 'package:provider/provider.dart';
import 'package:redream_app/core/auth/auth_service.dart';
import 'package:redream_app/core/settings/app_settings.dart';
import 'package:redream_app/core/storage/secure_audio_cache.dart';
import 'package:redream_app/core/utils/errors.dart';
import 'package:redream_app/core/widgets/app_button.dart';
import 'package:redream_app/core/widgets/app_card.dart';
import 'package:redream_app/core/widgets/feedback.dart';
import 'package:redream_app/features/journal/dream.dart';
import 'package:redream_app/features/journal/journal_state.dart';
import 'package:redream_app/features/journal/practice_helpers.dart';
import 'package:redream_app/l10n/generated/app_localizations.dart';

class PracticeAudioScreen extends StatefulWidget {
  const PracticeAudioScreen({required this.dream, super.key});

  final Dream dream;

  @override
  State<PracticeAudioScreen> createState() => _PracticeAudioScreenState();
}

class _PracticeAudioScreenState extends State<PracticeAudioScreen> {
  final _player = AudioPlayer();
  late Dream _dream = widget.dream;
  bool _loading = true;
  String? _error;
  bool _saving = false;
  bool _downloading = false;
  File? _playbackFile;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _load());
  }

  @override
  void dispose() {
    final playback = _playbackFile;
    unawaited(
      _player.dispose().then((_) async {
        if (playback != null && playback.parent.existsSync()) {
          await playback.parent.delete(recursive: true);
        }
      }),
    );
    super.dispose();
  }

  String get _language => AppSettings.normalizeLanguage(
    _dream.rehearsalLanguageOr(context.read<AppSettings>().language),
  );

  /// Audio in [_language]. Audio made in another language (an older dream
  /// whose chat switched language) counts as missing, so it is regenerated.
  bool get _hasAudio =>
      (_dream.rehearsalAudioUrl ?? '').isNotEmpty &&
      (_dream.rehearsalLanguage == null ||
          _dream.rehearsalLanguage == _language);

  Future<File> _cacheFile() async {
    final owner = context.read<AuthService>().userId;
    if (owner == null) throw StateError('Sign-in required');
    return SecureAudioCache.file(
      owner,
      _dream.id,
      _language,
      _dream.rewrittenDream ?? '',
    );
  }

  Future<void> _playCached(File file) async {
    final playback = await SecureAudioCache.decodeForPlayback(file);
    if (!mounted) {
      await playback.parent.delete(recursive: true);
      return;
    }
    final previous = _playbackFile;
    _playbackFile = playback;
    await _player.setFilePath(playback.path);
    if (previous != null && previous.parent.existsSync()) {
      await previous.parent.delete(recursive: true);
    }
  }

  Future<void> _load() async {
    final l10n = AppLocalizations.of(context)!;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      if (_dream.isLocal) throw _UserError(l10n.audioSyncFirst);
      if (!_dream.hasRewrite) throw _UserError(l10n.audioNoRewritten);

      final cached = await _cacheFile();
      if (cached.existsSync()) {
        await _playCached(cached);
        return;
      }
      final journal = context.read<JournalState>();
      if (!_hasAudio) {
        _dream = await journal.reload(_dream.id);
      }
      if (!_hasAudio && mounted) {
        _dream = await journal.generateRehearsal(_dream, language: _language);
      }
      if ((_dream.rehearsalAudioUrl ?? '').isNotEmpty) {
        _dream = await journal.refreshRehearsal(_dream.id);
      }
      final url = _dream.rehearsalAudioUrl ?? '';
      if (url.isEmpty) throw _UserError(l10n.audioUnavailable);
      final recheck = await _cacheFile();
      if (recheck.existsSync()) {
        await _playCached(recheck);
      } else {
        await _player.setUrl(journal.rehearsalUri(url).toString());
      }
    } on Exception catch (e) {
      if (mounted) {
        _error = e is _UserError ? e.message : describeError(e, l10n);
      }
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _download() async {
    final l10n = AppLocalizations.of(context)!;
    setState(() => _downloading = true);
    try {
      final file = await _cacheFile();
      if (file.existsSync()) {
        showAppSnackBar(context, l10n.audioAlreadySaved);
        return;
      }
      final journal = context.read<JournalState>();
      _dream = await journal.refreshRehearsal(_dream.id);
      final url = _dream.rehearsalAudioUrl ?? '';
      if (url.isEmpty) throw _UserError(l10n.audioUnavailable);
      final bytes = await journal.downloadRehearsal(url);
      await file.parent.create(recursive: true);
      await SecureAudioCache.save(file, bytes);
      if (mounted) showAppSnackBar(context, l10n.audioSaved);
    } on Exception catch (e) {
      if (mounted) {
        final reason = e is _UserError ? e.message : describeError(e, l10n);
        showAppSnackBar(
          context,
          l10n.audioDownloadFailed(reason),
          error: true,
        );
      }
    } finally {
      if (mounted) setState(() => _downloading = false);
    }
  }

  Future<void> _done() async {
    setState(() => _saving = true);
    await _player.pause();
    if (mounted) await completePractice(context, _dream);
    if (mounted) setState(() => _saving = false);
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(
        leading: IconButton(
          icon: const Icon(Icons.close),
          onPressed: () => Navigator.pop(context),
        ),
        title: Text(l10n.practiceAudioTitle),
      ),
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(16),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 520),
              child: AppCard(
                padding: const EdgeInsets.all(20),
                child: _loading
                    ? _Loading(text: l10n.audioLoading)
                    : _error != null
                    ? _Error(
                        title: l10n.audioLoadFailed,
                        message: _error!,
                        onRetry: _load,
                      )
                    : Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          Row(
                            children: [
                              const IconTile(
                                icon: Icons.graphic_eq_rounded,
                                size: 56,
                              ),
                              const SizedBox(width: 14),
                              Expanded(
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text(
                                      _dream.title.isEmpty
                                          ? l10n.practiceAudioTitle
                                          : _dream.title,
                                      style: theme.textTheme.titleLarge,
                                    ),
                                    // The player's own length, so it
                                    // always matches the slider below.
                                    StreamBuilder<Duration?>(
                                      stream: _player.durationStream,
                                      initialData: _player.duration,
                                      builder: (_, snap) => Text(
                                        switch (snap.data) {
                                          final d? => _PlayerControls._fmt(d),
                                          null => l10n.audioSubtitleDefault,
                                        },
                                        style: theme.textTheme.bodyMedium
                                            ?.copyWith(
                                              color: theme
                                                  .colorScheme
                                                  .onSurfaceVariant,
                                            ),
                                      ),
                                    ),
                                  ],
                                ),
                              ),
                            ],
                          ),
                          const SizedBox(height: 20),
                          _PlayerControls(player: _player),
                          if ((_dream.rehearsalScript ?? '')
                              .trim()
                              .isNotEmpty) ...[
                            const SizedBox(height: 20),
                            Text(
                              l10n.audioScript,
                              style: theme.textTheme.titleMedium,
                            ),
                            const SizedBox(height: 8),
                            ConstrainedBox(
                              constraints: const BoxConstraints(maxHeight: 220),
                              child: SingleChildScrollView(
                                child: Text(
                                  _dream.rehearsalScript!,
                                  style: theme.textTheme.bodyMedium?.copyWith(
                                    height: 1.5,
                                  ),
                                ),
                              ),
                            ),
                          ],
                          const SizedBox(height: 20),
                          AppButton(
                            label: l10n.audioSaveOffline,
                            icon: Icons.download_outlined,
                            style: AppButtonStyle.outlined,
                            busy: _downloading,
                            onPressed: _download,
                          ),
                          const SizedBox(height: 10),
                          AppButton(
                            label: l10n.done,
                            busy: _saving,
                            onPressed: _done,
                          ),
                        ],
                      ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _UserError implements Exception {
  _UserError(this.message);
  final String message;
}

class _Loading extends StatelessWidget {
  const _Loading({required this.text});
  final String text;

  @override
  Widget build(BuildContext context) => Column(
    children: [
      const Padding(
        padding: EdgeInsets.all(16),
        child: CircularProgressIndicator(),
      ),
      Text(text),
    ],
  );
}

class _Error extends StatelessWidget {
  const _Error({
    required this.title,
    required this.message,
    required this.onRetry,
  });

  final String title;
  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Icon(Icons.error_outline, size: 40, color: theme.colorScheme.error),
        const SizedBox(height: 12),
        Text(
          title,
          textAlign: TextAlign.center,
          style: theme.textTheme.titleMedium,
        ),
        const SizedBox(height: 6),
        Text(message, textAlign: TextAlign.center),
        const SizedBox(height: 20),
        AppButton(label: l10n.audioTryAgain, onPressed: onRetry),
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: Text(l10n.close),
        ),
      ],
    );
  }
}

class _PlayerControls extends StatelessWidget {
  const _PlayerControls({required this.player});

  final AudioPlayer player;

  static String _fmt(Duration d) {
    final m = d.inMinutes.remainder(60).toString().padLeft(2, '0');
    final s = d.inSeconds.remainder(60).toString().padLeft(2, '0');
    return '$m:$s';
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Column(
      children: [
        StreamBuilder<PlayerState>(
          stream: player.playerStateStream,
          builder: (_, snap) {
            final state = snap.data;
            final busy =
                state?.processingState == ProcessingState.loading ||
                state?.processingState == ProcessingState.buffering;
            final playing = state?.playing ?? false;
            final completed =
                state?.processingState == ProcessingState.completed;
            return SizedBox(
              width: 64,
              height: 64,
              child: busy
                  ? const Padding(
                      padding: EdgeInsets.all(16),
                      child: CircularProgressIndicator(),
                    )
                  : IconButton.filled(
                      iconSize: 36,
                      icon: Icon(
                        playing && !completed ? Icons.pause : Icons.play_arrow,
                      ),
                      onPressed: () async {
                        if (completed) await player.seek(Duration.zero);
                        if (playing && !completed) {
                          await player.pause();
                        } else {
                          await player.play();
                        }
                      },
                    ),
            );
          },
        ),
        StreamBuilder<Duration?>(
          stream: player.durationStream,
          builder: (_, durSnap) {
            final total = durSnap.data ?? Duration.zero;
            return StreamBuilder<Duration>(
              stream: player.positionStream,
              builder: (_, posSnap) {
                final pos = posSnap.data ?? Duration.zero;
                final max = total.inMilliseconds.toDouble();
                return Column(
                  children: [
                    Slider(
                      value: max == 0
                          ? 0
                          : pos.inMilliseconds.clamp(0, max.toInt()).toDouble(),
                      max: max == 0 ? 1 : max,
                      onChanged: max == 0
                          ? null
                          : (v) =>
                                player.seek(Duration(milliseconds: v.round())),
                    ),
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Text(
                          _fmt(pos),
                          style: TextStyle(color: scheme.onSurfaceVariant),
                        ),
                        Text(
                          _fmt(total),
                          style: TextStyle(color: scheme.onSurfaceVariant),
                        ),
                      ],
                    ),
                  ],
                );
              },
            );
          },
        ),
      ],
    );
  }
}
