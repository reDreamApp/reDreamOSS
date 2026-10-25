import 'dart:async';

import 'package:flutter/foundation.dart';

import 'package:redream_app/core/api/api_exception.dart';
import 'package:redream_app/features/chat/chat_models.dart';
import 'package:redream_app/features/chat/chat_service.dart';
import 'package:redream_app/features/chat/voice_recorder.dart';

class ChatState extends ChangeNotifier {
  ChatState(
    this._service, {
    IVoiceRecorder? voiceRecorder,
    this.onSessionDone,
  }) : _recorder = voiceRecorder;

  /// Called when a session finishes live: the backend has just created its
  /// dream, so journal and progress need a reload.
  final VoidCallback? onSessionDone;

  /// [errorMessage] values the UI localizes instead of showing verbatim.
  static const offline = 'offline';
  static const streamInterrupted = 'stream interrupted';
  static const noChat = 'no_chat';
  static const rateLimited = 'rate_limited';
  static const voiceUnavailable = 'voice_unavailable';
  static const serverError = 'server_error';

  final IChatService _service;

  /// Null when the platform has no recorder wired (tests, unsupported
  /// targets) — the composer hides the mic button entirely.
  final IVoiceRecorder? _recorder;

  int? _chatId;

  /// The current chat's language as the backend reports it; null when
  /// unknown (no chat, or a chat missing from the loaded list).
  String? _chatLanguage;
  IrtStage _stage = IrtStage.recording;
  final List<ChatMessage> _messages = [];
  bool _sending = false;
  bool _isDone = false;
  bool _resumedDone = false;
  SafetyEvent? _safetyEvent;
  int? _retryAfterSeconds;
  String? _errorMessage;
  bool _voiceOff = false;
  bool _startFailed = false;
  bool _intensitySubmitted = false;
  bool _intensitySaving = false;
  bool _recording = false;
  bool _transcribing = false;
  bool _micPermissionDenied = false;
  String? _failedMessageId;
  bool _hasStreamingDraft = false;
  int _localSeq = 0;

  /// Bumped whenever the visible session is rebound ([startChat], [loadChat]
  /// of a different chat, [deleteChat] of the active chat). An in-flight
  /// [send] captures the value and stops applying stream events once it goes
  /// stale, so a reply streaming for one chat can never leak into another
  /// chat's message list or stage. A same-chat reload (pull-to-refresh) is
  /// not a rebind and does not bump the epoch.
  int _sessionEpoch = 0;

  List<ChatSummary> _chats = const [];
  bool _chatsLoading = false;
  String? _chatsError;
  bool _loadingChat = false;

  int? get chatId => _chatId;
  IrtStage get stage => _stage;
  List<ChatMessage> get messages => List.unmodifiable(_messages);
  bool get sending => _sending;

  /// True while a send is in flight but no streamed draft bubble is showing
  /// yet — the window in which the UI shows the "thinking" indicator. Once
  /// deltas flow, the growing draft bubble itself is the feedback.
  bool get awaitingReply => _sending && !_hasStreamingDraft;
  bool get isDone => _isDone;
  SafetyEvent? get safetyEvent => _safetyEvent;

  /// Set while the daily-limit banner shows (the server's daily cap).
  int? get retryAfterSeconds => _retryAfterSeconds;
  String? get errorMessage => _errorMessage;

  /// True when the last [startChat] call failed; the UI offers a retry.
  bool get startFailed => _startFailed;

  /// True once the 0-10 rating for this session's dream was saved; the
  /// finished panel hides the rating card. Resets when the session rebinds.
  bool get intensitySubmitted => _intensitySubmitted;

  /// True while the rating save is in flight (disables the card's controls).
  bool get intensitySaving => _intensitySaving;

  /// True when a recorder is wired and the server still takes voice — the
  /// composer shows the mic button.
  bool get voiceInputAvailable => _recorder != null && !_voiceOff;

  /// True while the mic is capturing audio.
  bool get recording => _recording;

  /// True while a stopped recording is being transcribed.
  bool get transcribing => _transcribing;

  /// True when the last [startRecording] was refused mic permission; the UI
  /// shows a localized hint. Cleared on the next attempt and by
  /// [dismissError].
  bool get micPermissionDenied => _micPermissionDenied;

  /// Local id of the message whose send failed, if any.
  String? get failedMessageId => _failedMessageId;

  /// True when the current chat was already finished when it was loaded
  /// (resumed from history) — the UI shows it read-only, without the
  /// "start new session" call to action.
  bool get resumedDone => _resumedDone;

  /// The user's chats, newest first (drawer contents).
  List<ChatSummary> get chats => List.unmodifiable(_chats);
  bool get chatsLoading => _chatsLoading;
  String? get chatsError => _chatsError;

  /// True while [loadChat] is fetching a chat's messages.
  bool get loadingChat => _loadingChat;

  /// Starts a new chat session, resetting all local state.
  ///
  /// [welcomeMessages] are the localized, display-only greetings; the chat
  /// shows one of them as its first assistant bubble (see [_greeting]). It is
  /// never sent to the backend — chat history is owned by the backend
  /// (ADR 0002); this mirrors the legacy client.
  Future<void> startChat({
    required String language,
    required List<String> welcomeMessages,
  }) async {
    _startFailed = false;
    _errorMessage = null;
    try {
      final res = await _service.start(language: language);
      _sessionEpoch++;
      // Rebinding the session: no send is in flight for the NEW session,
      // so the composer re-enables (a dying stale stream must not touch it).
      _sending = false;
      _hasStreamingDraft = false;
      _chatId = res.chatId;
      _chatLanguage = res.language;
      _stage = res.currentStage;
      _messages.clear();
      if (_greeting(res.chatId, welcomeMessages) case final greeting?) {
        _messages.add(_localMessage(role: 'assistant', content: greeting));
      }
      _isDone = false;
      _resumedDone = false;
      _intensitySubmitted = false;
      _intensitySaving = false;
      _safetyEvent = null;
      _retryAfterSeconds = null;
      _failedMessageId = null;
    } on ApiException catch (e) {
      _startFailed = true;
      _errorMessage = _apiMessage(e);
    } on Exception catch (e) {
      _startFailed = true;
      _errorMessage = _describe(e);
    } finally {
      notifyListeners();
    }
  }

  /// First-open flow: resume the most recent chat when it is still open,
  /// otherwise start a fresh one. Chat history lives on the backend — this
  /// refetches `/chats` + `/chats/{id}/messages` instead of reconstructing
  /// anything locally.
  Future<void> resumeOrStart({
    required String language,
    required List<String> welcomeMessages,
  }) async {
    try {
      _chats = _sortedNewestFirst(await _service.listChats());
      _chatsError = null;
      notifyListeners();
    } on Exception {
      // Fall through — startChat below surfaces the failure (retry banner).
      _chats = const [];
    }
    final latest = _chats.isEmpty ? null : _chats.first;
    if (latest != null && !latest.isDone) {
      if (latest.title == null && latest.language != language) {
        // Nobody has written in it yet, and it was started before the app's
        // language changed: its greeting would be in the app's language
        // while the guide answers in the other (2026-10-25).
        await deleteChat(latest.id);
      } else {
        final loaded = await loadChat(
          latest.id,
          welcomeMessages: welcomeMessages,
        );
        if (loaded) return;
      }
    }
    await startChat(language: language, welcomeMessages: welcomeMessages);
  }

  /// After the app's language changed: a chat nobody has written in yet is
  /// replaced by a new one in that language. A chat with messages keeps its
  /// language; the backend switches it when the user writes in the other.
  Future<void> followAppLanguage({
    required String language,
    required List<String> welcomeMessages,
  }) async {
    final id = _chatId;
    if (id == null || _chatLanguage == null || _chatLanguage == language) {
      return;
    }
    if (_sending || _loadingChat || _messages.any((m) => m.isUser)) return;
    await deleteChat(id);
    await startChat(language: language, welcomeMessages: welcomeMessages);
  }

  /// Refreshes the chat list shown in the history drawer.
  Future<void> loadChats() async {
    _chatsLoading = true;
    _chatsError = null;
    notifyListeners();
    try {
      _chats = _sortedNewestFirst(await _service.listChats());
    } on ApiException catch (e) {
      _chatsError = e.message;
    } on Exception catch (e) {
      _chatsError = e.toString();
    } finally {
      _chatsLoading = false;
      notifyListeners();
    }
  }

  /// Loads an existing chat from the backend (drawer tap / resume / refresh).
  ///
  /// [welcomeMessages] are the display-only greetings. The chat's one is
  /// never stored, so every load puts it back as the first bubble, dated
  /// like the chat's first message.
  /// Returns false (and sets [errorMessage]) when loading failed.
  Future<bool> loadChat(int id, {List<String>? welcomeMessages}) async {
    _loadingChat = true;
    _errorMessage = null;
    _startFailed = false;
    notifyListeners();
    try {
      final fetched = await _service.listMessages(id);
      ChatSummary? summary;
      for (final c in _chats) {
        if (c.id == id) summary = c;
      }
      if (id != _chatId) {
        // Rebinding to a DIFFERENT chat: in-flight sends for the old session
        // stop applying (epoch guard) and no send is in flight for the new
        // session, so the composer re-enables. A same-chat reload
        // (pull-to-refresh) is not a rebind — a still-running stream keeps
        // applying; send() dedupes against the refetched turn by message id.
        _sessionEpoch++;
        _sending = false;
        _hasStreamingDraft = false;
        _intensitySubmitted = false;
        _intensitySaving = false;
      }
      _chatId = id;
      _chatLanguage = summary?.language;
      _messages.clear();
      if (_greeting(id, welcomeMessages) case final greeting?) {
        _messages.add(
          _localMessage(
            role: 'assistant',
            content: greeting,
            createdAt: fetched.firstOrNull?.createdAt,
          ),
        );
      }
      _messages.addAll(fetched);
      _isDone = summary?.isDone ?? false;
      _resumedDone = _isDone;
      if (_isDone) {
        _stage = IrtStage.finalStage;
      } else if (summary != null) {
        _stage = summary.currentStage;
      } else {
        _stage = _stageFromMessages(fetched);
      }
      _safetyEvent = null;
      _retryAfterSeconds = null;
      _failedMessageId = null;
      return true;
    } on ApiException catch (e) {
      _errorMessage = _apiMessage(e);
      return false;
    } on Exception catch (e) {
      _errorMessage = _describe(e);
      return false;
    } finally {
      _loadingChat = false;
      notifyListeners();
    }
  }

  /// Pull-to-refresh: reloads the current chat's messages (and the chat
  /// list, so done/title state stays accurate) from the backend.
  Future<void> refreshCurrentChat({List<String>? welcomeMessages}) async {
    final id = _chatId;
    if (id == null) return;
    try {
      _chats = _sortedNewestFirst(await _service.listChats());
    } on Exception {
      // Keep the stale list; loadChat below reports the real error.
    }
    await loadChat(id, welcomeMessages: welcomeMessages);
  }

  /// Deletes a chat on the backend and locally. When the active chat is
  /// deleted, the local session is cleared (callers start a fresh one).
  Future<bool> deleteChat(int id) async {
    try {
      await _service.deleteChat(id);
    } on Exception {
      return false;
    }
    _chats = _chats.where((c) => c.id != id).toList();
    if (_chatId == id) {
      _sessionEpoch++;
      // Rebinding the session: no send is in flight for the new session.
      _sending = false;
      _hasStreamingDraft = false;
      _chatId = null;
      _chatLanguage = null;
      _messages.clear();
      _stage = IrtStage.recording;
      _isDone = false;
      _resumedDone = false;
      _intensitySubmitted = false;
      _intensitySaving = false;
      _safetyEvent = null;
      _retryAfterSeconds = null;
      _failedMessageId = null;
    }
    notifyListeners();
    return true;
  }

  void dismissSafetyEvent() {
    _safetyEvent = null;
    notifyListeners();
  }

  void dismissError() {
    _errorMessage = null;
    _retryAfterSeconds = null;
    _micPermissionDenied = false;
    notifyListeners();
  }

  /// Starts capturing a voice note for the composer. Recording is
  /// composer-scoped, not chat-scoped: [stopRecording] hands the transcript
  /// to the chat screen, which sends it into the chat that is still open.
  Future<void> startRecording() async {
    final recorder = _recorder;
    if (recorder == null || _recording || _transcribing || _sending) return;
    _micPermissionDenied = false;
    _errorMessage = null;
    try {
      if (!await recorder.hasPermission()) {
        _micPermissionDenied = true;
        return;
      }
      await recorder.start();
      _recording = true;
    } on Exception catch (e) {
      _errorMessage = _describe(e);
    } finally {
      notifyListeners();
    }
  }

  /// Stops the recording, transcribes it, and returns the transcript (null
  /// when nothing was captured or transcription failed — failures surface
  /// via [errorMessage] / [retryAfterSeconds] like every other API error).
  Future<String?> stopRecording() async {
    final recorder = _recorder;
    if (recorder == null || !_recording) return null;
    _recording = false;
    _transcribing = true;
    notifyListeners();
    try {
      final recording = await recorder.stop();
      if (recording == null) return null;
      final text = await _service.transcribeAudio(
        bytes: recording.bytes,
        fileName: recording.fileName,
      );
      return text.trim().isEmpty ? null : text.trim();
    } on ApiException catch (e) {
      if (e.isDailyLimit) {
        _retryAfterSeconds = e.retryAfterSeconds ?? 0;
      } else if (e.isVoiceUnavailable) {
        // The server's info was older than its settings: no mic from now on.
        _voiceOff = true;
        _errorMessage = voiceUnavailable;
      } else {
        _errorMessage = _apiMessage(e);
      }
      return null;
    } on Exception catch (e) {
      _errorMessage = _describe(e);
      return null;
    } finally {
      _transcribing = false;
      notifyListeners();
    }
  }

  /// Discards an in-progress recording without transcribing.
  Future<void> cancelRecording() async {
    final recorder = _recorder;
    if (recorder == null || !_recording) return;
    _recording = false;
    try {
      await recorder.cancel();
    } on Exception {
      // Discard-only path; nothing to surface.
    }
    notifyListeners();
  }

  @override
  void dispose() {
    unawaited(_recorder?.dispose());
    super.dispose();
  }

  Future<void> send(String content) async {
    final id = _chatId;
    if (id == null) {
      _errorMessage = noChat;
      notifyListeners();
      return;
    }
    if (content.trim().isEmpty) return;
    // Optimistically show the user's message so a failed send never
    // discards what they typed.
    final local = _localMessage(role: 'user', content: content);
    _messages.add(local);
    _failedMessageId = null;
    _sending = true;
    _hasStreamingDraft = false;
    _safetyEvent = null;
    _retryAfterSeconds = null;
    _errorMessage = null;
    notifyListeners();

    // The bubble currently holding the user's text: the optimistic local one
    // until the backend's persisted user_message frame replaces it.
    var userMessage = local;
    // Local assistant bubble whose content grows with each delta; replaced
    // by the persisted ai_message on done, dropped on safety/error. Content
    // accumulates in [draftContent]; [draftIndex] tracks the bubble's slot
    // in _messages so applying a delta is O(1) instead of an indexOf scan.
    ChatMessage? draft;
    var draftIndex = -1;
    final draftContent = StringBuffer();
    // Whether a terminal frame (done / safety / error) was seen.
    var terminal = false;
    // The session this send belongs to. Once the user rebinds the visible
    // session mid-stream (drawer tap, new chat, delete), every event below
    // would mutate ANOTHER chat's messages/stage — so we stop applying them.
    final epoch = _sessionEpoch;
    bool stale() => _sessionEpoch != epoch;

    // True when [draftIndex] still points at the draft bubble — a same-chat
    // reload may have rebuilt _messages underneath a running stream.
    bool draftInPlace() =>
        draft != null &&
        draftIndex >= 0 &&
        draftIndex < _messages.length &&
        identical(_messages[draftIndex], draft);

    void removeDraft() {
      final current = draft;
      if (current == null) return;
      if (draftInPlace()) {
        _messages.removeAt(draftIndex);
      } else {
        _messages.remove(current); // identity-based; no-op after a reload
      }
      draft = null;
      draftIndex = -1;
      _hasStreamingDraft = false;
    }

    try {
      final events = _service.sendMessageStream(chatId: id, content: content);
      await for (final event in events) {
        if (stale()) {
          // The server already persisted its side; reloading the chat shows
          // the turn. One exception: a safety event MUST still surface
          // (ADR 0005 — the helpline is about the user, not a specific chat).
          if (event is SafetyEventEvent) {
            _safetyEvent = event.safetyEvent;
            notifyListeners();
          }
          break; // leaves the await-for, cancelling the subscription
        }
        switch (event) {
          case UserMessageEvent(:final message):
            if (_messages.any((m) => m.id == message.id)) {
              // A same-chat reload already fetched the persisted message —
              // drop the optimistic bubble instead of adding a duplicate.
              final index = _messages.indexOf(userMessage);
              if (index != -1) {
                _messages.removeAt(index);
                if (index < draftIndex) draftIndex--;
              }
            } else {
              _replace(userMessage, message);
            }
            userMessage = message;
          case ContentDeltaEvent(:final delta):
            draftContent.write(delta);
            final current = draft;
            final grown = current == null
                ? _localMessage(
                    role: 'assistant',
                    content: draftContent.toString(),
                  )
                : ChatMessage(
                    id: current.id,
                    role: current.role,
                    content: draftContent.toString(),
                    stage: current.stage,
                    createdAt: current.createdAt,
                  );
            if (draftInPlace()) {
              _messages[draftIndex] = grown;
            } else {
              // First delta, or a same-chat reload rebuilt the list — the
              // buffer holds the full reply so far, so re-adding loses
              // nothing.
              _messages.add(grown);
              draftIndex = _messages.length - 1;
            }
            draft = grown;
            _hasStreamingDraft = true;
          case DoneEvent(:final aiMessage, :final stage, :final isDone):
            removeDraft();
            // A same-chat reload may have already fetched the finalized
            // turn — don't add the persisted ai message twice.
            if (!_messages.any((m) => m.id == aiMessage.id)) {
              _messages.add(aiMessage);
            }
            _stage = stage;
            _isDone = isDone;
            terminal = true;
            if (isDone) {
              // The list still holds this chat as unfinished; the home
              // screen counts finished sessions from it.
              unawaited(loadChats());
              onSessionDone?.call();
            }
          case SafetyEventEvent(:final safetyEvent):
            // Mandatory helpline handling (ADR 0005): surface the event; no
            // AI turn happened and the stage does NOT advance.
            removeDraft();
            _safetyEvent = safetyEvent;
            terminal = true;
          case ErrorEvent():
            // The backend rolled back the user message — mark the bubble
            // failed so the existing retry flow can resend it. The frame's
            // text is the server's exception, not for people.
            removeDraft();
            _failedMessageId = userMessage.id;
            _errorMessage = serverError;
            terminal = true;
        }
        notifyListeners();
      }
      if (!terminal && !stale()) {
        // Stream ended without done/safety/error (connection dropped). The
        // server may nevertheless have finished and persisted the turn —
        // reconcile against the backend before declaring the send failed,
        // so a retry can't duplicate a server-persisted turn.
        var reconciled = false;
        try {
          final fetched = await _service.listMessages(id);
          if (!stale() && _turnPersisted(fetched, content)) {
            _messages
              ..clear()
              ..addAll(fetched);
            draft = null;
            draftIndex = -1;
            _hasStreamingDraft = false;
            _stage = _stageFromMessages(fetched);
            _failedMessageId = null;
            _errorMessage = null;
            reconciled = true;
          }
        } on Exception {
          // Reconcile fetch failed — fall back to the failed/retry path.
        }
        if (!reconciled && !stale()) {
          // The turn is absent server-side (rolled back): treat like a
          // mid-stream error so the retry flow can resend.
          removeDraft();
          _failedMessageId = userMessage.id;
          _errorMessage = streamInterrupted;
        }
      }
    } on ApiException catch (e) {
      if (stale()) {
        // The failure belongs to a session that is no longer on screen —
        // touching _messages / _failedMessageId / the 409 is-done state
        // would corrupt the WRONG chat. But session-INDEPENDENT feedback
        // still surfaces: the daily limit is per user, so its banner is
        // global, and other failures still deserve an error banner. The 409
        // "chat is finished" case is purely session-scoped and stays
        // skipped.
        if (e.isDailyLimit) {
          _retryAfterSeconds = e.retryAfterSeconds ?? 0;
        } else if (e.statusCode != 409) {
          _errorMessage = _apiMessage(e);
        }
      } else {
        removeDraft();
        if (e.statusCode == 409) {
          // The chat finished on the backend (stale local state) — drop the
          // optimistic message and show the finished, read-only view.
          _messages.remove(userMessage);
          _isDone = true;
          _resumedDone = true;
          _stage = IrtStage.finalStage;
        } else if (e.isDailyLimit) {
          _failedMessageId = userMessage.id;
          _retryAfterSeconds = e.retryAfterSeconds ?? 0;
        } else {
          _failedMessageId = userMessage.id;
          _errorMessage = _apiMessage(e);
        }
      }
    } on Exception catch (e) {
      // Transport failure mid-stream: keep the user's bubble and let the
      // existing retry flow resend it (the backend rolls back on failure).
      if (!stale()) {
        removeDraft();
        _failedMessageId = userMessage.id;
        _errorMessage = _describe(e);
      }
    } finally {
      if (!stale()) {
        // A stale send belongs to a replaced session — its end must not
        // clear the flags of a send that may be running for the CURRENT
        // session (the rebind already reset them).
        _sending = false;
        _hasStreamingDraft = false;
      }
      notifyListeners();
    }
  }

  /// True when [fetched] shows the server completed the turn for [content]:
  /// the last user message matches the sent content and at least one
  /// assistant message follows it.
  bool _turnPersisted(List<ChatMessage> fetched, String content) {
    for (var i = fetched.length - 1; i >= 0; i--) {
      if (!fetched[i].isUser) continue;
      return fetched[i].content == content &&
          fetched.skip(i + 1).any((m) => !m.isUser);
    }
    return false;
  }

  /// Replaces [from] with [to] in place (appends when [from] is gone).
  void _replace(ChatMessage from, ChatMessage to) {
    final index = _messages.indexOf(from);
    if (index == -1) {
      _messages.add(to);
    } else {
      _messages[index] = to;
    }
  }

  /// Re-sends the message whose send previously failed.
  Future<void> retryFailedSend() async {
    final failedId = _failedMessageId;
    if (failedId == null || _sending) return;
    final index = _messages.indexWhere((m) => m.id == failedId);
    if (index == -1) {
      _failedMessageId = null;
      return;
    }
    final content = _messages.removeAt(index).content;
    _failedMessageId = null;
    await send(content);
  }

  /// Saves the 0-10 rating on the dream created from this chat. The routed
  /// chat no longer elicits the rating, so the app collects it when the
  /// session finishes. The card stays available until a save succeeds.
  Future<void> submitIntensity(int value) async {
    final id = _chatId;
    if (id == null || _intensitySaving) return;
    // Capture the session before awaiting: when the user rebinds mid-save
    // (drawer tap / new chat / delete), the result must not mark the NEW
    // session's rating as submitted.
    final epoch = _sessionEpoch;
    _intensitySaving = true;
    notifyListeners();
    var saved = false;
    try {
      await _service.setDreamIntensity(chatId: id, value: value);
      saved = true;
    } on ApiException catch (e) {
      if (_sessionEpoch == epoch) _errorMessage = _apiMessage(e);
    } on Exception catch (e) {
      if (_sessionEpoch == epoch) _errorMessage = _describe(e);
    } finally {
      if (_sessionEpoch == epoch) {
        _intensitySaving = false;
        if (saved) _intensitySubmitted = true;
      }
      notifyListeners();
    }
  }

  static String _describe(Exception e) =>
      e is NetworkException ? offline : e.toString();

  /// The server's detail, or a marker the UI localizes.
  static String _apiMessage(ApiException e) => e.isVoiceUnavailable
      ? voiceUnavailable
      : e.isRateLimited
      ? rateLimited
      : e.isServerError
      ? serverError
      : e.message;

  /// The greeting of one chat. It follows the chat's id, so a reload shows
  /// the same one and new chats cycle through the variants.
  static String? _greeting(int chatId, List<String>? greetings) =>
      greetings == null || greetings.isEmpty
      ? null
      : greetings[chatId % greetings.length];

  ChatMessage _localMessage({
    required String role,
    required String content,
    DateTime? createdAt,
  }) => ChatMessage(
    id: 'local-${_localSeq++}',
    role: role,
    content: content,
    stage: null,
    createdAt: createdAt ?? DateTime.now(),
  );

  List<ChatSummary> _sortedNewestFirst(List<ChatSummary> list) =>
      [...list]..sort((a, b) => b.updatedAt.compareTo(a.updatedAt));

  IrtStage _stageFromMessages(List<ChatMessage> fetched) {
    for (final m in fetched.reversed) {
      final s = m.stage;
      if (s != null) return stageFromString(s);
    }
    return IrtStage.recording;
  }
}
