import 'dart:async';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:redream_app/core/api/api_exception.dart';
import 'package:redream_app/features/chat/chat_models.dart';
import 'package:redream_app/features/chat/chat_service.dart';
import 'package:redream_app/features/chat/chat_state.dart';
import 'package:redream_app/features/chat/voice_recorder.dart';

class _FakeVoiceRecorder implements IVoiceRecorder {
  bool permission = true;
  bool started = false;
  bool cancelled = false;
  bool disposed = false;

  /// Bytes returned by [stop]; null simulates an empty capture.
  Uint8List? captured = Uint8List.fromList([1, 2, 3]);

  @override
  Future<bool> hasPermission() async => permission;

  @override
  Future<void> start() async => started = true;

  @override
  Future<VoiceRecording?> stop() async {
    final bytes = captured;
    if (bytes == null) return null;
    return VoiceRecording(bytes: bytes, fileName: 'dream_recording.wav');
  }

  @override
  Future<void> cancel() async => cancelled = true;

  @override
  Future<void> dispose() async => disposed = true;
}

ChatSummary _chat(
  int id, {
  bool isDone = false,
  IrtStage stage = IrtStage.recording,
  String? title,
  String language = 'en',
  DateTime? updatedAt,
}) => ChatSummary(
  id: id,
  language: language,
  currentStage: stage,
  isDone: isDone,
  isNightmare: null,
  title: title,
  createdAt: DateTime.utc(2026, 1, id),
  updatedAt: updatedAt ?? DateTime.utc(2026, 1, id),
);

class _FakeChatService implements IChatService {
  bool failStart = false;
  bool failSend = false;
  ApiException sendError = ApiException(500, 'boom');
  IrtStage nextStage = IrtStage.recording;
  bool nextIsDone = false;
  SafetyEvent? nextSafetyEvent;

  /// Content deltas emitted before the done frame on the normal path.
  List<String> nextDeltas = const [];

  /// When set, [sendMessageStream] returns this instead of the default
  /// scripted turn. Single-subscription: tests reset it between sends.
  Stream<ChatStreamEvent>? scriptedStream;
  int _id = 0;

  List<ChatSummary> chatList = [];
  bool failListChats = false;
  Map<int, List<ChatMessage>> messagesByChat = {};
  bool failListMessages = false;
  bool failDelete = false;
  final List<int> deletedIds = [];
  int listChatsCalls = 0;
  int listMessagesCalls = 0;

  @override
  Future<ChatStart> start({required String language}) async {
    if (failStart) throw ApiException(500, 'start failed');
    return ChatStart(
      chatId: 1,
      language: language,
      currentStage: IrtStage.recording,
    );
  }

  @override
  Future<List<ChatSummary>> listChats() async {
    listChatsCalls++;
    if (failListChats) throw ApiException(500, 'list failed');
    return chatList;
  }

  @override
  Future<List<ChatMessage>> listMessages(int chatId) async {
    listMessagesCalls++;
    if (failListMessages) throw ApiException(500, 'messages failed');
    return messagesByChat[chatId] ?? [];
  }

  @override
  Future<ChatTurn> sendMessage({
    required int chatId,
    required String content,
  }) async {
    if (failSend) throw sendError;
    return ChatTurn(
      userMessage: _msg('user', content),
      aiMessage: _msg('assistant', 'reply to $content'),
      stage: nextStage,
      isDone: nextIsDone,
      safetyEvent: nextSafetyEvent,
    );
  }

  @override
  Stream<ChatStreamEvent> sendMessageStream({
    required int chatId,
    required String content,
  }) => scriptedStream ?? _turn(content);

  Stream<ChatStreamEvent> _turn(String content) async* {
    if (failSend) throw sendError;
    yield UserMessageEvent(_msg('user', content));
    final safety = nextSafetyEvent;
    if (safety != null) {
      // High-risk safety path: no AI turn, no done frame.
      yield SafetyEventEvent(safety);
      return;
    }
    for (final delta in nextDeltas) {
      yield ContentDeltaEvent(delta);
    }
    yield DoneEvent(
      aiMessage: _msg('assistant', 'reply to $content'),
      stage: nextStage,
      isDone: nextIsDone,
    );
  }

  @override
  Future<void> deleteChat(int chatId) async {
    if (failDelete) throw ApiException(500, 'delete failed');
    deletedIds.add(chatId);
  }

  bool failSetIntensity = false;

  /// When set, [setDreamIntensity] awaits this before completing — lets a
  /// test hold a save in flight across a session switch.
  Future<void>? intensityGate;
  final List<(int, int)> intensityCalls = [];

  @override
  Future<void> setDreamIntensity({
    required int chatId,
    required int value,
  }) async {
    final gate = intensityGate;
    if (gate != null) await gate;
    if (failSetIntensity) throw ApiException(500, 'intensity failed');
    intensityCalls.add((chatId, value));
  }

  bool failTranscribe = false;
  ApiException transcribeError = ApiException(500, 'transcribe failed');
  String transcribeResult = 'a transcribed dream';
  final List<(int, String)> transcribeCalls = [];

  @override
  Future<String> transcribeAudio({
    required Uint8List bytes,
    required String fileName,
  }) async {
    if (failTranscribe) throw transcribeError;
    transcribeCalls.add((bytes.length, fileName));
    return transcribeResult;
  }

  ChatMessage _msg(String role, String content, {String? stage}) => ChatMessage(
    id: 'srv-${_id++}',
    role: role,
    content: content,
    stage: stage,
    createdAt: DateTime.utc(2026),
  );

  ChatMessage serverMessage(String role, String content, {String? stage}) =>
      _msg(role, content, stage: stage);
}

void main() {
  late _FakeChatService service;
  late ChatState state;

  setUp(() {
    service = _FakeChatService();
    state = ChatState(service);
  });

  Future<void> start() =>
      state.startChat(language: 'en', welcomeMessages: ['Welcome!']);

  group('startChat', () {
    test('inserts local welcome message on success', () async {
      await start();
      expect(state.chatId, 1);
      expect(state.startFailed, isFalse);
      expect(state.messages, hasLength(1));
      expect(state.messages.single.content, 'Welcome!');
      expect(state.messages.single.isUser, isFalse);
    });

    test('sets startFailed and errorMessage on failure', () async {
      service.failStart = true;
      await start();
      expect(state.startFailed, isTrue);
      expect(state.errorMessage, isNotNull);
      expect(state.messages, isEmpty);
    });

    test('resets done/intensity/error state', () async {
      await start();
      service
        ..nextIsDone = true
        ..nextStage = IrtStage.finalStage;
      await state.send('bye');
      expect(state.isDone, isTrue);
      await state.submitIntensity(7);
      expect(state.intensitySubmitted, isTrue);

      await start();
      expect(state.isDone, isFalse);
      expect(state.resumedDone, isFalse);
      expect(state.intensitySubmitted, isFalse);
      expect(state.errorMessage, isNull);
      expect(state.failedMessageId, isNull);
      expect(state.messages, hasLength(1));
    });
  });

  group('resumeOrStart', () {
    Future<void> resume() =>
        state.resumeOrStart(language: 'en', welcomeMessages: ['Welcome!']);

    test('resumes the most recent chat when it is not done', () async {
      service.chatList = [
        _chat(7, updatedAt: DateTime.utc(2026, 5)),
        _chat(3, isDone: true, updatedAt: DateTime.utc(2026, 4)),
      ];
      service.messagesByChat[7] = [
        service.serverMessage('user', 'my dream', stage: 'recording'),
        service.serverMessage('assistant', 'tell me more', stage: 'recording'),
      ];
      await resume();
      expect(state.chatId, 7);
      expect(state.isDone, isFalse);
      // The display-only welcome bubble comes back above the history.
      expect(state.messages.map((m) => m.content), [
        'Welcome!',
        'my dream',
        'tell me more',
      ]);
    });

    test('sorts chats newest first before picking', () async {
      service.chatList = [
        _chat(2, isDone: true, updatedAt: DateTime.utc(2026, 5, 2)),
        _chat(9, updatedAt: DateTime.utc(2026, 5)),
      ];
      await resume();
      // Newest chat (id 2) is done -> start fresh instead of resuming id 9.
      expect(state.chatId, 1);
      expect(state.messages.single.content, 'Welcome!');
    });

    test('does not resume an untouched chat in another language', () async {
      // Started while the app was German, then the user switched to English
      // (2026-10-25: the greeting was English, the guide answered German).
      service.chatList = [_chat(7, language: 'de')];
      await resume();
      expect(service.deletedIds, [7]);
      expect(state.chatId, 1);
      expect(state.messages.single.content, 'Welcome!');
    });

    test('resumes a chat in another language once it has messages', () async {
      service.chatList = [_chat(7, language: 'de', title: 'Mein Traum')];
      service.messagesByChat[7] = [
        service.serverMessage('user', 'Mein Traum', stage: 'recording'),
      ];
      await resume();
      expect(service.deletedIds, isEmpty);
      expect(state.chatId, 7);
    });

    test('starts fresh when there are no chats', () async {
      await resume();
      expect(state.chatId, 1);
      expect(state.messages.single.content, 'Welcome!');
    });

    test(
      'falls back to startChat (and its error path) when listing fails',
      () async {
        service
          ..failListChats = true
          ..failStart = true;
        await resume();
        expect(state.startFailed, isTrue);
        expect(state.errorMessage, isNotNull);
      },
    );
  });

  group('followAppLanguage', () {
    test('replaces a chat nobody has written in yet', () async {
      await state.startChat(language: 'de', welcomeMessages: ['Willkommen!']);
      await state.followAppLanguage(
        language: 'en',
        welcomeMessages: ['Welcome!'],
      );
      expect(service.deletedIds, [1]);
      expect(state.chatId, 1);
      expect(state.messages.single.content, 'Welcome!');
    });

    test('keeps a chat in the same language', () async {
      await state.startChat(language: 'en', welcomeMessages: ['Welcome!']);
      await state.followAppLanguage(
        language: 'en',
        welcomeMessages: ['Welcome!'],
      );
      expect(service.deletedIds, isEmpty);
    });

    test('keeps a chat the user has written in', () async {
      service.chatList = [_chat(5, language: 'de', title: 'Traum')];
      await state.loadChats();
      service.messagesByChat[5] = [
        service.serverMessage('user', 'Traum', stage: 'recording'),
      ];
      await state.loadChat(5);
      await state.followAppLanguage(
        language: 'en',
        welcomeMessages: ['Welcome!'],
      );
      expect(service.deletedIds, isEmpty);
      expect(state.chatId, 5);
    });
  });

  group('loadChat', () {
    test('loads messages and stage from the chat summary', () async {
      service.chatList = [_chat(5, stage: IrtStage.rewriting)];
      await state.loadChats();
      service.messagesByChat[5] = [
        service.serverMessage('user', 'dream', stage: 'recording'),
        service.serverMessage('assistant', 'rewrite it', stage: 'rewriting'),
      ];
      final ok = await state.loadChat(5);
      expect(ok, isTrue);
      expect(state.chatId, 5);
      expect(state.stage, IrtStage.rewriting);
      expect(state.isDone, isFalse);
      expect(state.resumedDone, isFalse);
      expect(state.messages, hasLength(2));
    });

    test('marks a done chat as resumed read-only with final stage', () async {
      service.chatList = [_chat(5, isDone: true, stage: IrtStage.summary)];
      await state.loadChats();
      service.messagesByChat[5] = [
        service.serverMessage('user', 'dream', stage: 'recording'),
      ];
      await state.loadChat(5);
      expect(state.isDone, isTrue);
      expect(state.resumedDone, isTrue);
      expect(state.stage, IrtStage.finalStage);
    });

    test('inserts the welcome bubble when the chat has no messages', () async {
      service.chatList = [_chat(5)];
      await state.loadChats();
      await state.loadChat(5, welcomeMessages: ['Welcome!']);
      expect(state.messages.single.content, 'Welcome!');
    });

    test('puts the welcome bubble back above a reloaded chat', () async {
      service.chatList = [_chat(5)];
      await state.loadChats();
      service.messagesByChat[5] = [
        service.serverMessage('user', 'dream', stage: 'recording'),
        service.serverMessage('assistant', 'go on', stage: 'recording'),
      ];
      await state.loadChat(5, welcomeMessages: ['Welcome!']);
      final welcome = state.messages.first;
      expect(welcome.content, 'Welcome!');
      expect(welcome.id, startsWith('local-'));
      // Dated like the first stored message, not like the moment of loading.
      expect(welcome.createdAt, state.messages[1].createdAt);
      expect(state.messages, hasLength(3));
      // Reloading again must not stack a second greeting.
      await state.loadChat(5, welcomeMessages: ['Welcome!']);
      expect(state.messages, hasLength(3));
    });

    test('each chat keeps one greeting and new chats cycle through', () async {
      const greetings = ['one', 'two', 'three'];
      service.chatList = [_chat(3), _chat(4), _chat(5)];
      await state.loadChats();
      final shown = <String>[];
      for (final id in [3, 4, 5]) {
        await state.loadChat(id, welcomeMessages: greetings);
        shown.add(state.messages.first.content);
      }
      expect(shown, ['one', 'two', 'three']);
      // Opening a chat again shows the greeting it had before.
      await state.loadChat(4, welcomeMessages: greetings);
      expect(state.messages.first.content, 'two');
    });

    test('maps the legacy intensity stage to rewriting', () async {
      // Pre-port chats persisted an `intensity` routed stage; it renders as
      // the rewriting step now.
      expect(stageFromString('intensity'), IrtStage.rewriting);

      service.chatList = [_chat(5)];
      await state.loadChats();
      service.messagesByChat[5] = [
        service.serverMessage('assistant', 'rate it', stage: 'intensity'),
        service.serverMessage('user', '7', stage: 'intensity'),
      ];
      await state.loadChat(5);
      expect(state.stage, IrtStage.recording);
    });

    test('reports failure via errorMessage and return value', () async {
      service.failListMessages = true;
      final ok = await state.loadChat(5);
      expect(ok, isFalse);
      expect(state.errorMessage, ChatState.serverError);
    });
  });

  group('loadChats', () {
    test('sorts newest first', () async {
      service.chatList = [
        _chat(1, updatedAt: DateTime.utc(2026)),
        _chat(2, updatedAt: DateTime.utc(2026, 3)),
        _chat(3, updatedAt: DateTime.utc(2026, 2)),
      ];
      await state.loadChats();
      expect(state.chats.map((c) => c.id), [2, 3, 1]);
      expect(state.chatsError, isNull);
    });

    test('sets chatsError on failure', () async {
      service.failListChats = true;
      await state.loadChats();
      expect(state.chatsError, 'list failed');
      expect(state.chatsLoading, isFalse);
    });
  });

  group('deleteChat', () {
    test('removes the chat from the list', () async {
      service.chatList = [_chat(1), _chat(2)];
      await state.loadChats();
      final ok = await state.deleteChat(1);
      expect(ok, isTrue);
      expect(service.deletedIds, [1]);
      expect(state.chats.map((c) => c.id), [2]);
    });

    test('clears the active session when deleting the current chat', () async {
      await start();
      expect(state.chatId, 1);
      final ok = await state.deleteChat(1);
      expect(ok, isTrue);
      expect(state.chatId, isNull);
      expect(state.messages, isEmpty);
      expect(state.stage, IrtStage.recording);
    });

    test('returns false and keeps state on failure', () async {
      service.chatList = [_chat(1)];
      await state.loadChats();
      service.failDelete = true;
      final ok = await state.deleteChat(1);
      expect(ok, isFalse);
      expect(state.chats, hasLength(1));
    });
  });

  group('refreshCurrentChat', () {
    test('refetches messages and chat list for the current chat', () async {
      service.chatList = [_chat(4)];
      await state.loadChats();
      service.messagesByChat[4] = [
        service.serverMessage('user', 'hi', stage: 'recording'),
      ];
      await state.loadChat(4);
      expect(state.messages, hasLength(1));

      service.messagesByChat[4] = [
        service.serverMessage('user', 'hi', stage: 'recording'),
        service.serverMessage('assistant', 'hello', stage: 'recording'),
      ];
      await state.refreshCurrentChat();
      expect(state.messages, hasLength(2));
    });

    test('is a no-op without a current chat', () async {
      await state.refreshCurrentChat();
      expect(service.listMessagesCalls, 0);
    });
  });

  group('send', () {
    test('keeps typed message as failed bubble on error', () async {
      await start();
      service.failSend = true;
      await state.send('my dream');
      expect(state.messages.last.content, 'my dream');
      expect(state.failedMessageId, state.messages.last.id);
      expect(state.errorMessage, ChatState.serverError);
    });

    test('the daily limit sets retryAfterSeconds (not errorMessage)', () async {
      await start();
      service
        ..failSend = true
        ..sendError = ApiException(
          429,
          'daily limit reached',
          retryAfterSeconds: 30,
        );
      await state.send('my dream');
      expect(state.retryAfterSeconds, 30);
      expect(state.errorMessage, isNull);
      expect(state.failedMessageId, isNotNull);
    });

    test('another 429 is a short throttle, not the daily banner', () async {
      await start();
      service
        ..failSend = true
        ..sendError = ApiException(429, 'slow down', retryAfterSeconds: 5);
      await state.send('my dream');
      expect(state.retryAfterSeconds, isNull);
      expect(state.errorMessage, ChatState.rateLimited);
      expect(state.failedMessageId, isNotNull);
    });

    test('marks chat finished read-only on 409', () async {
      await start();
      service
        ..failSend = true
        ..sendError = ApiException(409, 'chat is already finished');
      await state.send('hello?');
      expect(state.isDone, isTrue);
      expect(state.resumedDone, isTrue);
      expect(state.stage, IrtStage.finalStage);
      expect(state.failedMessageId, isNull);
      // Optimistic bubble is dropped — only the welcome message remains.
      expect(state.messages, hasLength(1));
    });

    test('retryFailedSend resends the failed message', () async {
      await start();
      service.failSend = true;
      await state.send('my dream');
      service.failSend = false;
      await state.retryFailedSend();
      expect(state.failedMessageId, isNull);
      // welcome + server user message + ai reply
      expect(state.messages, hasLength(3));
      expect(state.messages[1].content, 'my dream');
      expect(state.messages[2].content, 'reply to my dream');
    });

    test('replaces optimistic message with server turn on success', () async {
      await start();
      await state.send('hello');
      expect(state.messages, hasLength(3));
      expect(state.messages[1].id, startsWith('srv-'));
    });
  });

  group('send (streaming)', () {
    test('deltas accumulate into a single growing assistant bubble', () async {
      await start();
      final controller = StreamController<ChatStreamEvent>();
      service.scriptedStream = controller.stream;
      final sendFuture = state.send('my dream');
      await pumpEventQueue();
      expect(state.sending, isTrue);

      controller.add(
        UserMessageEvent(service.serverMessage('user', 'my dream')),
      );
      await pumpEventQueue();
      // Optimistic bubble replaced by the persisted user message.
      expect(state.messages[1].id, startsWith('srv-'));

      controller.add(const ContentDeltaEvent('Tell '));
      await pumpEventQueue();
      expect(state.messages, hasLength(3));
      expect(state.messages.last.isUser, isFalse);
      expect(state.messages.last.content, 'Tell ');

      controller.add(const ContentDeltaEvent('me more.'));
      await pumpEventQueue();
      // Still one assistant bubble; its content grew in place.
      expect(state.messages, hasLength(3));
      expect(state.messages.last.content, 'Tell me more.');
      expect(state.sending, isTrue);

      controller.add(
        DoneEvent(
          aiMessage: service.serverMessage(
            'assistant',
            'Tell me more.',
            stage: 'rewriting',
          ),
          stage: IrtStage.rewriting,
          isDone: false,
        ),
      );
      await controller.close();
      await sendFuture;

      // Growing bubble replaced by the persisted ai_message; stage advanced.
      expect(state.messages, hasLength(3));
      expect(state.messages.last.id, startsWith('srv-'));
      expect(state.messages.last.content, 'Tell me more.');
      expect(state.stage, IrtStage.rewriting);
      expect(state.isDone, isFalse);
      expect(state.sending, isFalse);
      expect(state.failedMessageId, isNull);
    });

    test('done sets isDone; the rating can then be submitted', () async {
      await start();
      service
        ..nextDeltas = ['bye']
        ..nextStage = IrtStage.finalStage
        ..nextIsDone = true;
      await state.send('thanks');
      expect(state.isDone, isTrue);
      expect(state.stage, IrtStage.finalStage);
      expect(state.intensitySubmitted, isFalse);

      await state.submitIntensity(7);
      expect(state.intensitySubmitted, isTrue);
      expect(service.intensityCalls, [(1, 7)]);
    });

    test(
      'safety event removes the draft bubble and does not advance stage',
      () async {
        await start();
        service.scriptedStream = Stream.fromIterable([
          UserMessageEvent(service.serverMessage('user', 'dark thoughts')),
          const ContentDeltaEvent('I hear'),
          SafetyEventEvent(
            SafetyEvent(
              risk: 'high',
              categories: const ['self_harm'],
              confidence: 0.9,
              message: 'helpline',
            ),
          ),
        ]);
        await state.send('dark thoughts');
        expect(state.safetyEvent?.message, 'helpline');
        expect(state.stage, IrtStage.recording);
        expect(state.isDone, isFalse);
        // No AI turn: only welcome + persisted user message remain.
        expect(state.messages, hasLength(2));
        expect(state.messages.last.isUser, isTrue);
        expect(state.failedMessageId, isNull);
        expect(state.sending, isFalse);
      },
    );

    test('error frame removes the draft and enables the retry flow', () async {
      await start();
      service.scriptedStream = Stream.fromIterable([
        UserMessageEvent(service.serverMessage('user', 'my dream')),
        const ContentDeltaEvent('Tell '),
        const ErrorEvent('upstream failed'),
      ]);
      await state.send('my dream');
      // Draft dropped; the user bubble is marked failed.
      expect(state.messages, hasLength(2));
      expect(state.messages.last.content, 'my dream');
      expect(state.failedMessageId, state.messages.last.id);
      expect(state.errorMessage, ChatState.serverError);
      expect(state.sending, isFalse);

      service.scriptedStream = null;
      await state.retryFailedSend();
      expect(state.failedMessageId, isNull);
      expect(state.messages, hasLength(3));
      expect(state.messages[1].content, 'my dream');
      expect(state.messages[2].content, 'reply to my dream');
    });

    test('mid-stream transport exception enables the retry flow', () async {
      await start();
      final controller = StreamController<ChatStreamEvent>();
      service.scriptedStream = controller.stream;
      final sendFuture = state.send('my dream');
      await pumpEventQueue();
      controller
        ..add(UserMessageEvent(service.serverMessage('user', 'my dream')))
        ..add(const ContentDeltaEvent('Tell '))
        ..addError(Exception('connection lost'));
      await controller.close();
      await sendFuture;
      expect(state.messages, hasLength(2));
      expect(state.failedMessageId, state.messages.last.id);
      expect(state.errorMessage, isNotNull);
      expect(state.sending, isFalse);
    });

    test('stream ending without a terminal frame counts as failure', () async {
      await start();
      service.scriptedStream = Stream.fromIterable([
        UserMessageEvent(service.serverMessage('user', 'my dream')),
      ]);
      await state.send('my dream');
      expect(state.failedMessageId, state.messages.last.id);
      expect(state.errorMessage, isNotNull);
      expect(state.sending, isFalse);
    });
  });

  group('send (streaming) — interrupted-stream reconcile', () {
    test('reconciles a server-persisted turn instead of failing', () async {
      await start();
      final persistedUser = service.serverMessage(
        'user',
        'my dream',
        stage: 'recording',
      );
      final persistedAi = service.serverMessage(
        'assistant',
        'full reply',
        stage: 'rewriting',
      );
      // The server finished the turn even though the stream dropped.
      service.messagesByChat[1] = [persistedUser, persistedAi];
      service.scriptedStream = Stream.fromIterable([
        UserMessageEvent(persistedUser),
        const ContentDeltaEvent('full re'),
        // connection drops — no terminal frame
      ]);
      await state.send('my dream');

      expect(state.failedMessageId, isNull);
      expect(state.errorMessage, isNull);
      expect(state.messages.map((m) => m.id), [
        persistedUser.id,
        persistedAi.id,
      ]);
      expect(state.stage, IrtStage.rewriting);
      expect(state.sending, isFalse);
    });

    test('falls back to the retry flow when the server rolled back', () async {
      await start();
      service.messagesByChat[1] = []; // turn absent server-side
      service.scriptedStream = Stream.fromIterable([
        UserMessageEvent(service.serverMessage('user', 'my dream')),
        const ContentDeltaEvent('Tell '),
      ]);
      await state.send('my dream');
      expect(state.failedMessageId, state.messages.last.id);
      expect(state.errorMessage, 'stream interrupted');
      expect(state.sending, isFalse);

      service.scriptedStream = null;
      await state.retryFailedSend();
      expect(state.failedMessageId, isNull);
      expect(state.messages.last.content, 'reply to my dream');
    });

    test('falls back to the retry flow when the refetch fails', () async {
      await start();
      service
        ..scriptedStream = Stream.fromIterable([
          UserMessageEvent(service.serverMessage('user', 'my dream')),
        ])
        ..failListMessages = true;
      await state.send('my dream');
      expect(state.failedMessageId, state.messages.last.id);
      expect(state.errorMessage, 'stream interrupted');
      expect(state.sending, isFalse);
    });

    test(
      'malformed done frame is ignored and reconcile recovers the turn',
      () async {
        await start();
        final persistedUser = service.serverMessage(
          'user',
          'my dream',
          stage: 'recording',
        );
        final persistedAi = service.serverMessage(
          'assistant',
          'full reply',
          stage: 'rewriting',
        );
        service.messagesByChat[1] = [persistedUser, persistedAi];
        // Real SSE parsing: the malformed done frame maps to no event (no
        // crash), so the stream ends without a terminal frame and the
        // reconcile path recovers the persisted turn.
        service.scriptedStream = parseSseStream(
          Stream.fromIterable([
            'data: {"content": "full "}\n\n',
            'data: {"done": {"ai_message": "oops", "stage": 42}}\n\n',
            'data: [DONE]\n\n',
          ]),
        );
        await state.send('my dream');

        expect(state.errorMessage, isNull);
        expect(state.failedMessageId, isNull);
        expect(state.messages.map((m) => m.id), [
          persistedUser.id,
          persistedAi.id,
        ]);
        expect(state.stage, IrtStage.rewriting);
        expect(state.sending, isFalse);
      },
    );
  });

  group('send (streaming) — same-chat reload', () {
    test('reload mid-stream keeps the stream applying, done deduped', () async {
      await start(); // chat 1
      final controller = StreamController<ChatStreamEvent>();
      service.scriptedStream = controller.stream;
      final sendFuture = state.send('my dream');
      await pumpEventQueue();

      final persistedUser = service.serverMessage(
        'user',
        'my dream',
        stage: 'recording',
      );
      final persistedAi = service.serverMessage(
        'assistant',
        'Tell me more.',
        stage: 'rewriting',
      );
      controller
        ..add(UserMessageEvent(persistedUser))
        ..add(const ContentDeltaEvent('Tell '));
      await pumpEventQueue();
      expect(state.sending, isTrue);

      // Pull-to-refresh fetches the finalized turn while the stream runs.
      service.messagesByChat[1] = [persistedUser, persistedAi];
      await state.loadChat(1);
      // A same-chat reload is NOT a session rebind: the send stays active.
      expect(state.sending, isTrue);

      controller
        ..add(const ContentDeltaEvent('me more.'))
        ..add(
          DoneEvent(
            aiMessage: persistedAi,
            stage: IrtStage.rewriting,
            isDone: false,
          ),
        );
      await controller.close();
      await sendFuture;

      // The stream kept applying (stage advanced) without duplicating the
      // already-fetched turn.
      expect(state.stage, IrtStage.rewriting);
      expect(state.messages.map((m) => m.id), [
        persistedUser.id,
        persistedAi.id,
      ]);
      expect(state.sending, isFalse);
      expect(state.failedMessageId, isNull);
    });

    test('user_message frame after a reload drops the duplicate', () async {
      await start(); // chat 1
      final controller = StreamController<ChatStreamEvent>();
      service.scriptedStream = controller.stream;
      final sendFuture = state.send('my dream');
      await pumpEventQueue();

      final persistedUser = service.serverMessage(
        'user',
        'my dream',
        stage: 'recording',
      );
      // The reload lands BEFORE the user_message frame arrives.
      service.messagesByChat[1] = [persistedUser];
      await state.loadChat(1);

      controller.add(UserMessageEvent(persistedUser));
      await pumpEventQueue();
      expect(
        state.messages.where((m) => m.isUser && m.content == 'my dream'),
        hasLength(1),
      );

      controller.add(
        DoneEvent(
          aiMessage: service.serverMessage('assistant', 'reply'),
          stage: IrtStage.recording,
          isDone: false,
        ),
      );
      await controller.close();
      await sendFuture;
      expect(
        state.messages.map((m) => m.id).toSet(),
        hasLength(state.messages.length),
      );
    });
  });

  group('awaitingReply', () {
    test('is true only until the first delta creates the draft', () async {
      await start();
      final controller = StreamController<ChatStreamEvent>();
      service.scriptedStream = controller.stream;
      final sendFuture = state.send('my dream');
      await pumpEventQueue();
      expect(state.awaitingReply, isTrue);

      controller.add(
        UserMessageEvent(service.serverMessage('user', 'my dream')),
      );
      await pumpEventQueue();
      // Still no draft bubble — the thinking indicator stays.
      expect(state.awaitingReply, isTrue);

      controller.add(const ContentDeltaEvent('Tell '));
      await pumpEventQueue();
      // The draft bubble is the feedback now.
      expect(state.awaitingReply, isFalse);
      expect(state.sending, isTrue);

      controller.add(
        DoneEvent(
          aiMessage: service.serverMessage('assistant', 'Tell me.'),
          stage: IrtStage.recording,
          isDone: false,
        ),
      );
      await controller.close();
      await sendFuture;
      expect(state.awaitingReply, isFalse);
      expect(state.sending, isFalse);
    });
  });

  group('intensity', () {
    Future<void> finishChat() async {
      service
        ..nextStage = IrtStage.finalStage
        ..nextIsDone = true;
      await state.send('bye');
      expect(state.isDone, isTrue);
    }

    test(
      'submitIntensity saves the rating via the dedicated endpoint',
      () async {
        await start();
        await finishChat();
        expect(state.intensitySubmitted, isFalse);

        await state.submitIntensity(7);
        expect(state.intensitySubmitted, isTrue);
        expect(service.intensityCalls, [(1, 7)]);
        // The rating no longer travels as a chat message.
        expect(
          state.messages.where((m) => m.isUser && m.content == '7'),
          isEmpty,
        );
      },
    );

    test(
      'failed save keeps the card available and surfaces the error',
      () async {
        await start();
        await finishChat();
        service.failSetIntensity = true;
        await state.submitIntensity(7);
        expect(state.intensitySubmitted, isFalse);
        expect(state.errorMessage, ChatState.serverError);

        // A later retry can still succeed.
        service.failSetIntensity = false;
        state.dismissError();
        await state.submitIntensity(6);
        expect(state.intensitySubmitted, isTrue);
        expect(service.intensityCalls, [(1, 6)]);
      },
    );

    test('save abandoned by a session switch does not set the flag', () async {
      await start();
      await finishChat();

      final gate = Completer<void>();
      service.intensityGate = gate.future;
      final submitFuture = state.submitIntensity(7);
      await pumpEventQueue();

      // The user switches chats while the save is still in flight.
      service.messagesByChat[2] = [];
      await state.loadChat(2);
      gate.complete();
      await submitFuture;

      // The save belonged to a rebound session — the NEW session's rating
      // must not count as submitted (nor stay marked as saving).
      expect(state.intensitySubmitted, isFalse);
      expect(state.intensitySaving, isFalse);
    });
  });

  group('safety event', () {
    test('parses optional message field', () {
      final withMessage = SafetyEvent.fromJson(const {
        'risk': 'high',
        'categories': ['self_harm'],
        'confidence': 0.9,
        'message': 'Call 0800 111 0 111',
      });
      expect(withMessage.message, 'Call 0800 111 0 111');

      final withoutMessage = SafetyEvent.fromJson(const {
        'risk': 'high',
        'categories': ['self_harm'],
        'confidence': 0.9,
      });
      expect(withoutMessage.message, isNull);
    });

    test('is surfaced from a turn', () async {
      await start();
      service.nextSafetyEvent = SafetyEvent(
        risk: 'high',
        categories: const ['self_harm'],
        confidence: 0.9,
        message: 'helpline',
      );
      await state.send('dark thoughts');
      expect(state.safetyEvent?.message, 'helpline');
    });
  });

  group('send (streaming) — session switch guard', () {
    // Regression: a reply streaming for one chat must never leak into another
    // chat's message list or stage after the user rebinds the session
    // mid-stream (drawer tap / new chat / delete). The server persists the
    // turn either way; reloading the original chat shows it.
    test('events after loadChat of another chat are not applied', () async {
      await start();
      service.messagesByChat[2] = [
        service.serverMessage('user', 'older dream', stage: 'recording'),
        service.serverMessage('assistant', 'tell me more', stage: 'recording'),
      ];
      final controller = StreamController<ChatStreamEvent>();
      service.scriptedStream = controller.stream;
      final sendFuture = state.send('chat-1 message');
      await pumpEventQueue();

      controller
        ..add(UserMessageEvent(service.serverMessage('user', 'chat-1 message')))
        ..add(const ContentDeltaEvent('leaking '));
      await pumpEventQueue();

      // User opens the drawer and switches to chat 2 while chat 1 streams.
      await state.loadChat(2);
      final chat2Messages = List.of(state.messages);
      final chat2Stage = state.stage;

      controller
        ..add(const ContentDeltaEvent('content!'))
        ..add(
          DoneEvent(
            aiMessage: service.serverMessage('assistant', 'leaking content!'),
            stage: IrtStage.finalStage,
            isDone: true,
          ),
        );
      await controller.close();
      await sendFuture;

      // Chat 2's view is untouched by chat 1's turn.
      expect(
        state.messages.map((m) => m.content),
        chat2Messages.map((m) => m.content),
      );
      expect(state.messages.any((m) => m.content.contains('leaking')), isFalse);
      expect(state.stage, chat2Stage);
      expect(state.isDone, isFalse);
      expect(state.sending, isFalse);
      expect(state.failedMessageId, isNull);
    });

    test(
      'error after switching chats does not mark the new chat failed',
      () async {
        await start();
        service.messagesByChat[2] = [
          service.serverMessage(
            'assistant',
            'welcome back',
            stage: 'recording',
          ),
        ];
        final controller = StreamController<ChatStreamEvent>();
        service.scriptedStream = controller.stream;
        final sendFuture = state.send('chat-1 message');
        await pumpEventQueue();
        controller.add(
          UserMessageEvent(service.serverMessage('user', 'chat-1 message')),
        );
        await pumpEventQueue();

        await state.loadChat(2);
        controller.add(const ErrorEvent('upstream exploded'));
        await controller.close();
        await sendFuture;

        expect(state.failedMessageId, isNull);
        expect(state.errorMessage, isNull);
        expect(state.sending, isFalse);
      },
    );

    test(
      'safety event still surfaces across a chat switch (ADR 0005)',
      () async {
        await start();
        service.messagesByChat[2] = [];
        final controller = StreamController<ChatStreamEvent>();
        service.scriptedStream = controller.stream;
        final sendFuture = state.send('worrying message');
        await pumpEventQueue();
        controller.add(
          UserMessageEvent(service.serverMessage('user', 'worrying message')),
        );
        await pumpEventQueue();

        await state.loadChat(2);
        controller.add(
          SafetyEventEvent(
            SafetyEvent(
              risk: 'high',
              categories: const ['self_harm'],
              confidence: 0.9,
              message: 'helpline text',
            ),
          ),
        );
        await controller.close();
        await sendFuture;

        // The helpline is about the user, not a chat — it must never be
        // swallowed, even though the triggering chat left the screen.
        expect(state.safetyEvent, isNotNull);
        expect(state.safetyEvent!.message, 'helpline text');
        // But the switched-to chat's stage/messages stay untouched.
        expect(state.stage, IrtStage.recording);
        expect(
          state.messages.any((m) => m.content.contains('worrying')),
          isFalse,
        );
      },
    );

    test('switching chats mid-stream re-enables the composer', () async {
      await start();
      service.messagesByChat[2] = [];
      final controller = StreamController<ChatStreamEvent>();
      service.scriptedStream = controller.stream;
      final sendFuture = state.send('chat-1 message');
      await pumpEventQueue();
      expect(state.sending, isTrue);

      // Rebinding the session means no send is in flight for the NEW
      // session — the composer must re-enable immediately, not when the
      // abandoned stream eventually dies.
      await state.loadChat(2);
      expect(state.sending, isFalse);
      expect(state.awaitingReply, isFalse);

      await controller.close();
      await sendFuture;
      expect(state.sending, isFalse);
    });

    test("a dying stale stream cannot clear a newer send's flag", () async {
      await start();
      service.messagesByChat[2] = [];
      final first = StreamController<ChatStreamEvent>();
      service.scriptedStream = first.stream;
      final firstSend = state.send('chat-1 message');
      await pumpEventQueue();
      first.add(
        UserMessageEvent(service.serverMessage('user', 'chat-1 message')),
      );
      await pumpEventQueue();

      await state.loadChat(2);
      expect(state.sending, isFalse);

      // A new send starts for the new session.
      final second = StreamController<ChatStreamEvent>();
      service.scriptedStream = second.stream;
      final secondSend = state.send('chat-2 message');
      await pumpEventQueue();
      expect(state.sending, isTrue);

      // The abandoned chat-1 stream dies (no terminal frame): its finally
      // must not clear the chat-2 send's flag.
      await first.close();
      await firstSend;
      expect(state.sending, isTrue);

      second
        ..add(UserMessageEvent(service.serverMessage('user', 'chat-2 message')))
        ..add(
          DoneEvent(
            aiMessage: service.serverMessage('assistant', 'reply'),
            stage: IrtStage.recording,
            isDone: false,
          ),
        );
      await second.close();
      await secondSend;
      expect(state.sending, isFalse);
    });

    test(
      'stale pre-stream daily limit still raises the global banner',
      () async {
        await start();
        service.messagesByChat[2] = [];
        final controller = StreamController<ChatStreamEvent>();
        service.scriptedStream = controller.stream;
        final sendFuture = state.send('over the limit');
        await pumpEventQueue();

        await state.loadChat(2);
        controller.addError(
          ApiException(429, 'daily limit reached', retryAfterSeconds: 30),
        );
        await controller.close();
        await sendFuture;

        // The daily limit is per user, not per chat — the banner shows even
        // though the triggering chat left the screen.
        expect(state.retryAfterSeconds, 30);
        // Session-scoped effects stay off the new chat.
        expect(state.failedMessageId, isNull);
        expect(
          state.messages.any((m) => m.content == 'over the limit'),
          isFalse,
        );
      },
    );

    test('stale pre-stream error still surfaces errorMessage', () async {
      await start();
      service.messagesByChat[2] = [];
      final controller = StreamController<ChatStreamEvent>();
      service.scriptedStream = controller.stream;
      final sendFuture = state.send('doomed');
      await pumpEventQueue();

      await state.loadChat(2);
      controller.addError(ApiException(500, 'backend down'));
      await controller.close();
      await sendFuture;

      expect(state.errorMessage, ChatState.serverError);
      expect(state.failedMessageId, isNull);
      expect(state.messages.any((m) => m.content == 'doomed'), isFalse);
    });

    test('stale 409 stays session-scoped and is skipped', () async {
      await start();
      service.messagesByChat[2] = [];
      final controller = StreamController<ChatStreamEvent>();
      service.scriptedStream = controller.stream;
      final sendFuture = state.send('hello?');
      await pumpEventQueue();

      await state.loadChat(2);
      controller.addError(ApiException(409, 'chat is already finished'));
      await controller.close();
      await sendFuture;

      // The finished state belongs to the abandoned chat, not chat 2.
      expect(state.isDone, isFalse);
      expect(state.resumedDone, isFalse);
      expect(state.errorMessage, isNull);
      expect(state.failedMessageId, isNull);
    });

    test('startChat mid-stream discards the old turn events', () async {
      await start();
      final controller = StreamController<ChatStreamEvent>();
      service.scriptedStream = controller.stream;
      final sendFuture = state.send('first-chat message');
      await pumpEventQueue();
      controller
        ..add(
          UserMessageEvent(service.serverMessage('user', 'first-chat message')),
        )
        ..add(const ContentDeltaEvent('old reply'));
      await pumpEventQueue();

      await state.startChat(language: 'en', welcomeMessages: ['fresh start']);
      controller.add(
        DoneEvent(
          aiMessage: service.serverMessage('assistant', 'old reply'),
          stage: IrtStage.rewriting,
          isDone: false,
        ),
      );
      await controller.close();
      await sendFuture;

      expect(state.messages, hasLength(1)); // just the welcome bubble
      expect(state.messages.single.content, 'fresh start');
      expect(state.stage, IrtStage.recording);
    });
  });

  group('voice input', () {
    test('unavailable without a recorder — mic stays hidden', () {
      final state = ChatState(_FakeChatService());
      expect(state.voiceInputAvailable, isFalse);
    });

    test('record → stop transcribes and returns the transcript', () async {
      final service = _FakeChatService();
      final recorder = _FakeVoiceRecorder();
      final state = ChatState(service, voiceRecorder: recorder);

      await state.startRecording();
      expect(state.recording, isTrue);

      final transcript = await state.stopRecording();

      expect(transcript, 'a transcribed dream');
      expect(state.recording, isFalse);
      expect(state.transcribing, isFalse);
      // The recorded bytes reached the service (transcript is composer-only,
      // never sent as a chat message by the state).
      expect(service.transcribeCalls, [(3, 'dream_recording.wav')]);
      expect(state.messages, isEmpty);
    });

    test('permission denied sets the flag and never starts', () async {
      final recorder = _FakeVoiceRecorder()..permission = false;
      final state = ChatState(_FakeChatService(), voiceRecorder: recorder);

      await state.startRecording();

      expect(state.recording, isFalse);
      expect(state.micPermissionDenied, isTrue);
      state.dismissError();
      expect(state.micPermissionDenied, isFalse);
    });

    test('the daily limit during transcription shows its banner', () async {
      final service = _FakeChatService()
        ..failTranscribe = true
        ..transcribeError = ApiException(
          429,
          'daily limit reached',
          retryAfterSeconds: 60,
        );
      final recorder = _FakeVoiceRecorder();
      final state = ChatState(service, voiceRecorder: recorder);

      await state.startRecording();
      final transcript = await state.stopRecording();

      expect(transcript, isNull);
      expect(state.retryAfterSeconds, 60);
      expect(state.transcribing, isFalse);
    });

    test(
      'voice switched off on the server hides the mic from then on',
      () async {
        final service = _FakeChatService()
          ..failTranscribe = true
          ..transcribeError = ApiException(503, 'voice_unavailable');
        final state = ChatState(service, voiceRecorder: _FakeVoiceRecorder());
        expect(state.voiceInputAvailable, isTrue);

        await state.startRecording();
        final transcript = await state.stopRecording();

        expect(transcript, isNull);
        expect(state.errorMessage, ChatState.voiceUnavailable);
        expect(state.voiceInputAvailable, isFalse);
      },
    );

    test('transcription failure sets errorMessage, returns null', () async {
      final service = _FakeChatService()..failTranscribe = true;
      final recorder = _FakeVoiceRecorder();
      final state = ChatState(service, voiceRecorder: recorder);

      await state.startRecording();
      final transcript = await state.stopRecording();

      expect(transcript, isNull);
      expect(state.errorMessage, ChatState.serverError);
    });

    test('empty capture skips the transcription call', () async {
      final service = _FakeChatService();
      final recorder = _FakeVoiceRecorder()..captured = null;
      final state = ChatState(service, voiceRecorder: recorder);

      await state.startRecording();
      final transcript = await state.stopRecording();

      expect(transcript, isNull);
      expect(service.transcribeCalls, isEmpty);
      expect(state.errorMessage, isNull);
    });

    test('cancel discards without transcribing', () async {
      final service = _FakeChatService();
      final recorder = _FakeVoiceRecorder();
      final state = ChatState(service, voiceRecorder: recorder);

      await state.startRecording();
      await state.cancelRecording();

      expect(state.recording, isFalse);
      expect(recorder.cancelled, isTrue);
      expect(service.transcribeCalls, isEmpty);
    });
  });

  group('models', () {
    test('ChatSummary parses backend ChatOut json', () {
      final summary = ChatSummary.fromJson(const {
        'id': 12,
        'language': 'de',
        'current_stage': 'rewriting',
        'is_done': false,
        'is_nightmare': true,
        'title': 'Falling',
        'created_at': '2026-05-01T10:00:00Z',
        'updated_at': '2026-05-02T11:30:00Z',
      });
      expect(summary.id, 12);
      expect(summary.language, 'de');
      expect(summary.currentStage, IrtStage.rewriting);
      expect(summary.isDone, isFalse);
      expect(summary.isNightmare, isTrue);
      expect(summary.title, 'Falling');
      expect(summary.updatedAt, DateTime.utc(2026, 5, 2, 11, 30));
    });
  });

  group('ported additions', () {
    test('transport failures surface as the offline marker', () async {
      final service = _FakeChatService()
        ..scriptedStream = Stream.error(NetworkException('no route'));
      final state = ChatState(service);
      await state.startChat(language: 'en', welcomeMessages: ['hi']);
      await state.send('hello');
      expect(state.errorMessage, ChatState.offline);
      expect(state.failedMessageId, isNotNull);
    });
  });
}
