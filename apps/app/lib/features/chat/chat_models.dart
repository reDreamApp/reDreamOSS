/// The routed chat stages: recording → rewriting → summary → final.
/// `intensity` is no longer a routed stage — the ported core doesn't elicit
/// the 0-10 rating; the app collects it itself when a session finishes.
enum IrtStage { recording, rewriting, summary, finalStage }

IrtStage stageFromString(String s) {
  switch (s) {
    case 'recording':
      return IrtStage.recording;
    case 'intensity':
      // Legacy pre-port chats persisted this stage between recording and
      // rewriting; render such a chat as being in the rewriting step.
      return IrtStage.rewriting;
    case 'rewriting':
      return IrtStage.rewriting;
    case 'summary':
      return IrtStage.summary;
    case 'final':
      return IrtStage.finalStage;
  }
  return IrtStage.recording;
}

class ChatMessage {
  ChatMessage({
    required this.id,
    required this.role,
    required this.content,
    required this.stage,
    required this.createdAt,
  });

  factory ChatMessage.fromJson(Map<String, dynamic> json) => ChatMessage(
    id: json['id'].toString(),
    role: json['role'] as String,
    content: json['content'] as String,
    stage: json['stage'] as String?,
    createdAt: DateTime.parse(json['created_at'] as String),
  );

  final String id;
  final String role; // 'user' | 'assistant'
  final String content;
  final String? stage;
  final DateTime createdAt;

  bool get isUser => role == 'user';
}

/// One row of `GET /api/v1/chats` (backend `ChatOut`) — used by the
/// chat-history drawer and the resume-on-open flow.
class ChatSummary {
  ChatSummary({
    required this.id,
    required this.language,
    required this.currentStage,
    required this.isDone,
    required this.isNightmare,
    required this.title,
    required this.createdAt,
    required this.updatedAt,
  });

  factory ChatSummary.fromJson(Map<String, dynamic> json) => ChatSummary(
    id: json['id'] as int,
    language: json['language'] as String,
    currentStage: stageFromString(json['current_stage'] as String),
    isDone: json['is_done'] as bool? ?? false,
    isNightmare: json['is_nightmare'] as bool?,
    title: json['title'] as String?,
    createdAt: DateTime.parse(json['created_at'] as String),
    updatedAt: DateTime.parse(json['updated_at'] as String),
  );

  final int id;
  final String language;
  final IrtStage currentStage;
  final bool isDone;
  final bool? isNightmare;
  final String? title;
  final DateTime createdAt;
  final DateTime updatedAt;
}

class SafetyEvent {
  SafetyEvent({
    required this.risk,
    required this.categories,
    required this.confidence,
    this.message,
  });

  factory SafetyEvent.fromJson(Map<String, dynamic> json) => SafetyEvent(
    risk: json['risk'] as String,
    categories: (json['categories'] as List).cast<String>(),
    confidence: (json['confidence'] as num).toDouble(),
    message: json['message'] as String?,
  );

  final String risk;
  final List<String> categories;
  final double confidence;

  /// Optional backend-localized helpline text. Absent on older backends.
  final String? message;
}

class ChatStart {
  ChatStart({
    required this.chatId,
    required this.language,
    required this.currentStage,
  });

  factory ChatStart.fromJson(Map<String, dynamic> json) => ChatStart(
    chatId: json['chat_id'] as int,
    language: json['language'] as String,
    currentStage: stageFromString(json['current_stage'] as String),
  );

  final int chatId;
  final String language;
  final IrtStage currentStage;
}

/// One parsed SSE frame from `POST /chats/{id}/message/stream`.
///
/// `data: [DONE]` is handled by the SSE parser (it terminates the stream)
/// and never surfaces as an event.
sealed class ChatStreamEvent {
  const ChatStreamEvent();

  /// Maps a decoded SSE JSON frame to an event, or null for frames with no
  /// known key or a malformed known frame (per contract: ignore, don't
  /// crash). Extra keys inside a known frame (`stages`, `language`,
  /// `chat_id`, ...) are ignored.
  static ChatStreamEvent? fromJson(Map<String, dynamic> json) {
    final userMessage = json['user_message'];
    if (userMessage is Map<String, dynamic>) {
      final message = _messageOrNull(userMessage);
      return message == null ? null : UserMessageEvent(message);
    }
    final safetyEvent = json['safety_event'];
    if (safetyEvent is Map<String, dynamic>) {
      final event = _safetyEventOrNull(safetyEvent);
      return event == null ? null : SafetyEventEvent(event);
    }
    final content = json['content'];
    if (content is String) return ContentDeltaEvent(content);
    final done = json['done'];
    if (done is Map<String, dynamic>) {
      final Object? aiMessage = done['ai_message'];
      final Object? stage = done['stage'];
      final Object? isDone = done['is_done'];
      final message = aiMessage is Map<String, dynamic>
          ? _messageOrNull(aiMessage)
          : null;
      if (message == null || stage is! String) return null;
      return DoneEvent(
        aiMessage: message,
        stage: stageFromString(stage),
        isDone: isDone is bool && isDone,
      );
    }
    final error = json['error'];
    if (error is String) return ErrorEvent(error);
    return null;
  }

  /// [ChatMessage.fromJson] with `is` checks instead of casts: returns null
  /// for malformed payloads instead of throwing a [TypeError].
  static ChatMessage? _messageOrNull(Map<String, dynamic> json) {
    final Object? id = json['id'];
    final Object? role = json['role'];
    final Object? content = json['content'];
    final Object? stage = json['stage'];
    final Object? createdAt = json['created_at'];
    if (id == null || role is! String || content is! String) return null;
    if (stage is! String?) return null;
    if (createdAt is! String) return null;
    final created = DateTime.tryParse(createdAt);
    if (created == null) return null;
    return ChatMessage(
      id: id.toString(),
      role: role,
      content: content,
      stage: stage,
      createdAt: created,
    );
  }

  /// [SafetyEvent.fromJson] with `is` checks instead of casts: returns null
  /// for malformed payloads instead of throwing a [TypeError].
  static SafetyEvent? _safetyEventOrNull(Map<String, dynamic> json) {
    final Object? risk = json['risk'];
    final Object? categories = json['categories'];
    final Object? confidence = json['confidence'];
    final Object? message = json['message'];
    if (risk is! String || categories is! List || confidence is! num) {
      return null;
    }
    if (message is! String?) return null;
    if (categories.any((c) => c is! String)) return null;
    return SafetyEvent(
      risk: risk,
      categories: categories.cast<String>(),
      confidence: confidence.toDouble(),
      message: message,
    );
  }
}

/// The persisted user message — always the first frame of a stream.
class UserMessageEvent extends ChatStreamEvent {
  const UserMessageEvent(this.message);
  final ChatMessage message;
}

/// One incremental piece of the assistant's reply; append in order.
class ContentDeltaEvent extends ChatStreamEvent {
  const ContentDeltaEvent(this.delta);
  final String delta;
}

/// Terminal frame of the normal path: the persisted AI message plus the
/// resulting stage / done flag (same fields as [ChatTurn]).
class DoneEvent extends ChatStreamEvent {
  const DoneEvent({
    required this.aiMessage,
    required this.stage,
    required this.isDone,
  });

  final ChatMessage aiMessage;
  final IrtStage stage;
  final bool isDone;
}

/// Terminal frame of the high-risk safety path: no AI turn, the stage does
/// NOT advance (ADR 0005 — same semantics as the non-stream safety_event).
class SafetyEventEvent extends ChatStreamEvent {
  const SafetyEventEvent(this.safetyEvent);
  final SafetyEvent safetyEvent;
}

/// Mid-stream failure. The backend has rolled back the user message, so the
/// client marks the local message failed and offers the existing retry flow.
class ErrorEvent extends ChatStreamEvent {
  const ErrorEvent(this.message);
  final String message;
}

class ChatTurn {
  ChatTurn({
    required this.userMessage,
    required this.aiMessage,
    required this.stage,
    required this.isDone,
    required this.safetyEvent,
  });

  factory ChatTurn.fromJson(Map<String, dynamic> json) => ChatTurn(
    userMessage: ChatMessage.fromJson(
      json['user_message'] as Map<String, dynamic>,
    ),
    aiMessage: json['ai_message'] == null
        ? null
        : ChatMessage.fromJson(json['ai_message'] as Map<String, dynamic>),
    stage: stageFromString(json['stage'] as String),
    isDone: json['is_done'] as bool,
    safetyEvent: json['safety_event'] == null
        ? null
        : SafetyEvent.fromJson(json['safety_event'] as Map<String, dynamic>),
  );

  final ChatMessage userMessage;
  final ChatMessage? aiMessage;
  final IrtStage stage;
  final bool isDone;
  final SafetyEvent? safetyEvent;
}
