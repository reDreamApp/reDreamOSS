import 'dart:convert';

import 'package:flutter/foundation.dart';

import 'package:redream_app/core/api/api_client.dart';
import 'package:redream_app/features/chat/chat_models.dart';

abstract class IChatService {
  Future<ChatStart> start({required String language});
  Future<List<ChatSummary>> listChats();
  Future<List<ChatMessage>> listMessages(int chatId);
  Future<ChatTurn> sendMessage({required int chatId, required String content});

  /// Streams one turn via `POST /chats/{id}/message/stream` (SSE).
  ///
  /// Pre-stream failures (401/404/409/429/5xx) arrive as normal HTTP error
  /// responses and surface as the same `ApiException` [sendMessage] throws.
  Stream<ChatStreamEvent> sendMessageStream({
    required int chatId,
    required String content,
  });

  /// Saves the app-collected 0-10 intensity rating on the dream created
  /// from this chat (`PUT /chats/{id}/intensity`). The routed chat no
  /// longer elicits the rating — the app owns intensity collection.
  Future<void> setDreamIntensity({required int chatId, required int value});

  /// Transcribes a voice recording (`POST /audio/transcribe`, STT) and
  /// returns the transcript for the composer. Nothing is persisted — the
  /// audio is discarded server-side, the text only becomes part of the chat
  /// if the user sends it.
  Future<String> transcribeAudio({
    required Uint8List bytes,
    required String fileName,
  });

  Future<void> deleteChat(int chatId);
}

class ChatService implements IChatService {
  ChatService(this._api);
  final ApiClient _api;

  @override
  Future<ChatStart> start({required String language}) async {
    final json = await _api.post('/api/v1/chats/start', {'language': language});
    return ChatStart.fromJson(json);
  }

  @override
  Future<List<ChatSummary>> listChats() async {
    final list = await _api.getList('/api/v1/chats');
    return list
        .map((e) => ChatSummary.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  @override
  Future<List<ChatMessage>> listMessages(int chatId) async {
    final list = await _api.getList('/api/v1/chats/$chatId/messages');
    return list
        .map((e) => ChatMessage.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  @override
  Future<void> setDreamIntensity({
    required int chatId,
    required int value,
  }) async {
    await _api.put('/api/v1/chats/$chatId/intensity', {'intensity': value});
  }

  @override
  Future<String> transcribeAudio({
    required Uint8List bytes,
    required String fileName,
  }) async {
    final json = await _api.postMultipart(
      '/api/v1/audio/transcribe',
      bytes: bytes,
      fileName: fileName,
    );
    return (json['text'] as String?) ?? '';
  }

  @override
  Future<void> deleteChat(int chatId) => _api.delete('/api/v1/chats/$chatId');

  @override
  Future<ChatTurn> sendMessage({
    required int chatId,
    required String content,
  }) async {
    final json = await _api.post('/api/v1/chats/$chatId/message', {
      'content': content,
    }, aiTimeout);
    return ChatTurn.fromJson(json);
  }

  @override
  Stream<ChatStreamEvent> sendMessageStream({
    required int chatId,
    required String content,
  }) => parseSseStream(
    _api.postStream('/api/v1/chats/$chatId/message/stream', {
      'content': content,
    }),
  );
}

/// Parses a raw SSE text stream into [ChatStreamEvent]s.
///
/// Frames are `data: <payload>` blocks terminated by a blank line (`\n\n`);
/// a frame may span chunk boundaries, so input is buffered line by line
/// ([LineSplitter] keeps partial trailing lines until they complete).
/// `data: [DONE]` is always the terminal frame and ends the stream. Frames
/// that decode to something other than a JSON object, or JSON objects with
/// no known key, are ignored (per contract: ignore, don't crash).
@visibleForTesting
Stream<ChatStreamEvent> parseSseStream(Stream<String> chunks) async* {
  // `data:` payload lines of the frame currently being assembled. Multiple
  // data lines in one frame are joined with '\n' per the SSE spec; other
  // lines (comments, `event:`, ...) are ignored.
  final data = <String>[];
  await for (final line in chunks.transform(const LineSplitter())) {
    if (line.isEmpty) {
      // Blank line: end of frame.
      if (data.isEmpty) continue;
      final payload = data.join('\n');
      data.clear();
      if (payload == '[DONE]') return;
      final event = _eventFromData(payload);
      if (event != null) yield event;
      continue;
    }
    if (!line.startsWith('data:')) continue;
    var value = line.substring('data:'.length);
    if (value.startsWith(' ')) value = value.substring(1);
    data.add(value);
  }
}

ChatStreamEvent? _eventFromData(String data) {
  Object? decoded;
  try {
    decoded = jsonDecode(data);
  } on FormatException {
    return null;
  }
  if (decoded is! Map<String, dynamic>) return null;
  return ChatStreamEvent.fromJson(decoded);
}
