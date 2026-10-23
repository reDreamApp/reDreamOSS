/// A dream as served by `apps/api`, or a local draft (negative [id]) that has
/// not reached the server yet.
class Dream {
  const Dream({
    required this.id,
    required this.title,
    required this.originalDream,
    required this.isNightmare,
    required this.createdAt,
    required this.lastEdited,
    this.rewrittenDream,
    this.intensity,
    this.practiceTimestamps = const [],
    this.isCurrentDream = false,
    this.imageUrl,
    this.sessionId,
    this.source = 'manual',
    this.language,
    this.rehearsalAudioUrl,
    this.rehearsalScript,
    this.rehearsalDuration,
    this.rehearsalLanguage,
    this.rehearsalGeneratedAt,
  });

  factory Dream.fromJson(Map<String, dynamic> json) => Dream(
    id: json['id'] as int,
    title: json['title'] as String,
    originalDream: json['original_dream'] as String,
    rewrittenDream: json['rewritten_dream'] as String?,
    intensity: json['intensity'] as int?,
    isNightmare: json['is_nightmare'] as bool? ?? false,
    createdAt: DateTime.parse(json['created_at'] as String),
    lastEdited:
        DateTime.tryParse(json['last_edited'] as String? ?? '') ??
        DateTime.parse(json['created_at'] as String),
    practiceTimestamps: _parseTimestamps(json['practice_timestamps']),
    isCurrentDream: json['is_current_dream'] as bool? ?? false,
    imageUrl: json['image_url'] as String?,
    sessionId: json['session_id'] as String?,
    source: json['source'] as String? ?? 'manual',
    language: json['language'] as String?,
    rehearsalAudioUrl: json['rehearsal_audio_url'] as String?,
    rehearsalScript: json['rehearsal_script'] as String?,
    rehearsalDuration: json['rehearsal_duration'] as String?,
    rehearsalLanguage: json['rehearsal_language'] as String?,
    rehearsalGeneratedAt: DateTime.tryParse(
      json['rehearsal_generated_at'] as String? ?? '',
    ),
  );

  /// Practice sessions per nightmare shown in the journal.
  static const practiceTarget = 14;

  final int id;
  final String title;
  final String originalDream;
  final String? rewrittenDream;
  final int? intensity;
  final bool isNightmare;
  final DateTime createdAt;
  final DateTime lastEdited;
  final List<DateTime> practiceTimestamps;
  final bool isCurrentDream;
  final String? imageUrl;
  final String? sessionId;
  final String source;

  /// The dream's own language (`en`/`de`): the chat's language when the
  /// dream came out of a chat, the app's at creation for manual dreams. Null
  /// for older dreams and servers that do not send it.
  final String? language;
  final String? rehearsalAudioUrl;
  final String? rehearsalScript;
  final String? rehearsalDuration;
  final String? rehearsalLanguage;
  final DateTime? rehearsalGeneratedAt;

  bool get isLocal => id < 0;
  bool get hasRewrite => (rewrittenDream ?? '').trim().isNotEmpty;
  bool get canPractice => hasRewrite;
  int get practiceCount => practiceTimestamps.length;
  int get practiceRemaining =>
      (practiceTarget - practiceCount).clamp(0, practiceTarget);

  /// The chat this dream came out of (`session_id` holds the chat id).
  int? get chatId => int.tryParse(sessionId ?? '');

  /// Language to ask the audio rehearsal in: the dream's own, else the one
  /// its audio was made in, else [appLanguage]. The server lets the dream's
  /// language win as well.
  String rehearsalLanguageOr(String appLanguage) =>
      language ?? rehearsalLanguage ?? appLanguage;

  bool practicedOn(DateTime day) {
    final local = day.toLocal();
    return practiceTimestamps.any((t) {
      final l = t.toLocal();
      return l.year == local.year &&
          l.month == local.month &&
          l.day == local.day;
    });
  }

  Map<String, dynamic> toJson() => {
    'id': id,
    'title': title,
    'original_dream': originalDream,
    'rewritten_dream': rewrittenDream,
    'intensity': intensity,
    'is_nightmare': isNightmare,
    'created_at': createdAt.toIso8601String(),
    'last_edited': lastEdited.toIso8601String(),
    'practice_timestamps': [
      for (final t in practiceTimestamps) t.toIso8601String(),
    ],
    'is_current_dream': isCurrentDream,
    'image_url': imageUrl,
    'session_id': sessionId,
    'source': source,
    'language': language,
    'rehearsal_audio_url': rehearsalAudioUrl,
    'rehearsal_script': rehearsalScript,
    'rehearsal_duration': rehearsalDuration,
    'rehearsal_language': rehearsalLanguage,
    'rehearsal_generated_at': rehearsalGeneratedAt?.toIso8601String(),
  };

  Dream copyWith({
    int? id,
    String? title,
    String? originalDream,
    String? Function()? rewrittenDream,
    int? Function()? intensity,
    bool? isNightmare,
    DateTime? lastEdited,
    List<DateTime>? practiceTimestamps,
    bool? isCurrentDream,
  }) => Dream(
    id: id ?? this.id,
    title: title ?? this.title,
    originalDream: originalDream ?? this.originalDream,
    rewrittenDream: rewrittenDream == null
        ? this.rewrittenDream
        : rewrittenDream(),
    intensity: intensity == null ? this.intensity : intensity(),
    isNightmare: isNightmare ?? this.isNightmare,
    createdAt: createdAt,
    lastEdited: lastEdited ?? this.lastEdited,
    practiceTimestamps: practiceTimestamps ?? this.practiceTimestamps,
    isCurrentDream: isCurrentDream ?? this.isCurrentDream,
    imageUrl: imageUrl,
    sessionId: sessionId,
    source: source,
    language: language,
    rehearsalAudioUrl: rehearsalAudioUrl,
    rehearsalScript: rehearsalScript,
    rehearsalDuration: rehearsalDuration,
    rehearsalLanguage: rehearsalLanguage,
    rehearsalGeneratedAt: rehearsalGeneratedAt,
  );

  static List<DateTime> _parseTimestamps(Object? raw) => [
    if (raw is List)
      for (final t in raw) ?DateTime.tryParse(t.toString()),
  ];
}

/// What the editor collects; everything else is server-owned.
class DreamInput {
  const DreamInput({
    required this.title,
    required this.originalDream,
    required this.isNightmare,
    this.rewrittenDream,
    this.intensity,
    this.language,
  });

  final String title;
  final String originalDream;
  final bool isNightmare;
  final String? rewrittenDream;
  final int? intensity;

  /// The app's language, sent when the dream is created. Updates leave it
  /// out (`DreamService.update`): the server only takes it on create.
  final String? language;

  Map<String, dynamic> toJson() => {
    'title': title,
    'original_dream': originalDream,
    'rewritten_dream': rewrittenDream,
    'intensity': intensity,
    'is_nightmare': isNightmare,
    if (language != null) 'language': language,
  };
}

/// Dream waiting to be created ([remoteId] null) or updated on the server.
class DreamDraft {
  const DreamDraft({
    required this.localId,
    required this.input,
    required this.createdAt,
    required this.lastEdited,
    this.remoteId,
    this.practiceTimestamps = const [],
  });

  factory DreamDraft.fromJson(Map<String, dynamic> json) => DreamDraft(
    localId: json['local_id'] as int,
    remoteId: json['remote_id'] as int?,
    input: DreamInput(
      title: json['title'] as String,
      originalDream: json['original_dream'] as String,
      rewrittenDream: json['rewritten_dream'] as String?,
      intensity: json['intensity'] as int?,
      isNightmare: json['is_nightmare'] as bool? ?? false,
      language: json['language'] as String?,
    ),
    createdAt: DateTime.parse(json['created_at'] as String),
    lastEdited: DateTime.parse(json['last_edited'] as String),
    practiceTimestamps: Dream._parseTimestamps(json['practice_timestamps']),
  );

  final int localId;
  final int? remoteId;
  final DreamInput input;
  final DateTime createdAt;
  final DateTime lastEdited;
  final List<DateTime> practiceTimestamps;

  Map<String, dynamic> toJson() => {
    'local_id': localId,
    'remote_id': remoteId,
    ...input.toJson(),
    'created_at': createdAt.toIso8601String(),
    'last_edited': lastEdited.toIso8601String(),
    'practice_timestamps': [
      for (final t in practiceTimestamps) t.toIso8601String(),
    ],
  };

  /// Projection used by the journal; [remote] supplies server-only fields.
  Dream toDream({Dream? remote}) => Dream(
    id: localId,
    title: input.title,
    originalDream: input.originalDream,
    rewrittenDream: input.rewrittenDream,
    intensity: input.intensity,
    isNightmare: input.isNightmare,
    createdAt: remote?.createdAt ?? createdAt,
    lastEdited: lastEdited,
    practiceTimestamps: [...?remote?.practiceTimestamps, ...practiceTimestamps],
    isCurrentDream: remote?.isCurrentDream ?? false,
    sessionId: remote?.sessionId,
    source: remote?.source ?? 'manual',
    language: remote == null ? input.language : remote.language,
  );

  DreamDraft copyWith({
    DreamInput? input,
    DateTime? lastEdited,
    List<DateTime>? practiceTimestamps,
  }) => DreamDraft(
    localId: localId,
    remoteId: remoteId,
    input: input ?? this.input,
    createdAt: createdAt,
    lastEdited: lastEdited ?? this.lastEdited,
    practiceTimestamps: practiceTimestamps ?? this.practiceTimestamps,
  );
}
