import 'package:flutter/foundation.dart';
import 'package:redream_app/core/api/api_exception.dart';
import 'package:redream_app/features/journal/dream.dart';
import 'package:redream_app/features/journal/dream_service.dart';
import 'package:redream_app/features/journal/dream_store.dart';

enum DreamFilter { all, dreams, nightmares }

class SaveResult {
  const SaveResult(this.dream, {required this.savedOffline});
  final Dream dream;
  final bool savedOffline;
}

class PracticeResult {
  const PracticeResult(
    this.dream, {
    this.savedOffline = false,
    this.alreadyToday = false,
  });
  final Dream dream;
  final bool savedOffline;
  final bool alreadyToday;
}

/// Dreams for the signed-in user: server list merged with offline drafts.
class JournalState extends ChangeNotifier {
  JournalState(this._service, this._store, {this.onChanged}) {
    _dreams = _store.readCache();
    _drafts = _store.readDrafts();
    _pendingPractices = _store.readPendingPractices();
  }

  final DreamService _service;
  final DreamStore _store;

  /// Called after any mutation so dependent state (progress) can refresh.
  final VoidCallback? onChanged;

  List<Dream> _dreams = [];
  List<DreamDraft> _drafts = [];
  List<int> _pendingPractices = [];
  bool _loading = false;
  bool _loaded = false;
  bool _usedCache = false;
  Object? _error;
  bool _syncing = false;

  bool get loading => _loading;
  bool get loaded => _loaded;
  bool get usedCache => _usedCache;
  Object? get error => _error;
  bool get hasUnsynced => _drafts.isNotEmpty || _pendingPractices.isNotEmpty;

  /// Server dreams superseded by an edit draft are replaced by the draft.
  List<Dream> get dreams {
    final byRemote = {
      for (final d in _drafts) ?d.remoteId: d,
    };
    final merged = [
      for (final d in _dreams)
        if (!byRemote.containsKey(d.id)) d,
      for (final d in _drafts) d.toDream(remote: _remote(d.remoteId)),
    ];
    return _sorted(merged);
  }

  List<Dream> get nightmares => dreams.where((d) => d.isNightmare).toList();
  Dream? get focusDream => dreams.where((d) => d.isCurrentDream).firstOrNull;

  List<Dream> filtered(DreamFilter filter) => switch (filter) {
    DreamFilter.all => dreams,
    DreamFilter.dreams => dreams.where((d) => !d.isNightmare).toList(),
    DreamFilter.nightmares => nightmares,
  };

  Dream? byId(int id) => dreams.where((d) => d.id == id).firstOrNull;

  /// Practices recorded in the last 7 days across all dreams.
  int get practicesThisWeek {
    final since = DateTime.now().subtract(const Duration(days: 7));
    return dreams.fold(
      0,
      (n, d) => n + d.practiceTimestamps.where((t) => t.isAfter(since)).length,
    );
  }

  Future<void> load() async {
    if (_loaded || _loading) return;
    await refresh();
  }

  /// Fetches from the server; falls back to the cache when offline.
  Future<void> refresh() async {
    _loading = true;
    _error = null;
    notifyListeners();
    try {
      _dreams = await _service.list();
      _usedCache = false;
      await _store.writeCache(_dreams);
      await syncPending();
      await _ensureFocus();
    } on NetworkException {
      _usedCache = true;
    } on Exception catch (e) {
      _error = e;
    } finally {
      _loading = false;
      _loaded = true;
      notifyListeners();
    }
  }

  Future<SaveResult> create(DreamInput input) async {
    try {
      final dream = await _service.create(input);
      _dreams = [dream, ..._dreams];
      await _afterChange();
      return SaveResult(dream, savedOffline: false);
    } on NetworkException {
      final draft = DreamDraft(
        localId: -DateTime.now().millisecondsSinceEpoch,
        input: input,
        createdAt: DateTime.now(),
        lastEdited: DateTime.now(),
      );
      _drafts = [..._drafts, draft];
      await _store.writeDrafts(_drafts);
      notifyListeners();
      return SaveResult(draft.toDream(), savedOffline: true);
    }
  }

  Future<SaveResult> update(Dream dream, DreamInput input) async {
    if (dream.isLocal) {
      _drafts = [
        for (final d in _drafts)
          if (d.localId == dream.id)
            d.copyWith(input: input, lastEdited: DateTime.now())
          else
            d,
      ];
      await _store.writeDrafts(_drafts);
      notifyListeners();
      return SaveResult(byId(dream.id)!, savedOffline: true);
    }
    try {
      final updated = await _service.update(dream.id, input);
      _replace(updated);
      await _afterChange();
      return SaveResult(updated, savedOffline: false);
    } on NetworkException {
      final draft = DreamDraft(
        localId: -DateTime.now().millisecondsSinceEpoch,
        remoteId: dream.id,
        input: input,
        createdAt: dream.createdAt,
        lastEdited: DateTime.now(),
      );
      _drafts = [
        for (final d in _drafts)
          if (d.remoteId != dream.id) d,
        draft,
      ];
      await _store.writeDrafts(_drafts);
      notifyListeners();
      return SaveResult(draft.toDream(remote: dream), savedOffline: true);
    }
  }

  Future<void> delete(Dream dream) async {
    if (dream.isLocal) {
      _drafts = _drafts.where((d) => d.localId != dream.id).toList();
      await _store.writeDrafts(_drafts);
      notifyListeners();
      return;
    }
    await _service.delete(dream.id);
    _dreams = _dreams.where((d) => d.id != dream.id).toList();
    _drafts = _drafts.where((d) => d.remoteId != dream.id).toList();
    await _store.writeDrafts(_drafts);
    await _afterChange();
  }

  /// Optimistic; rolls back when the server rejects it.
  Future<void> setFocus(Dream dream) async {
    if (dream.isLocal) throw StateError('local draft');
    final before = _dreams;
    _dreams = [
      for (final d in _dreams) d.copyWith(isCurrentDream: d.id == dream.id),
    ];
    notifyListeners();
    try {
      _replace(await _service.setFocus(dream.id));
      await _afterChange();
    } on Exception catch (_) {
      _dreams = before;
      notifyListeners();
      rethrow;
    }
  }

  /// At most one practice per local calendar day, online or offline.
  Future<PracticeResult> recordPractice(Dream dream) async {
    final current = byId(dream.id) ?? dream;
    if (current.practicedOn(DateTime.now())) {
      return PracticeResult(current, alreadyToday: true);
    }
    if (dream.isLocal) {
      _drafts = [
        for (final d in _drafts)
          if (d.localId == dream.id)
            d.copyWith(
              practiceTimestamps: [
                ...d.practiceTimestamps,
                DateTime.now().toUtc(),
              ],
            )
          else
            d,
      ];
      await _store.writeDrafts(_drafts);
      notifyListeners();
      return PracticeResult(byId(dream.id)!, savedOffline: true);
    }
    try {
      final updated = await _service.recordPractice(dream.id);
      _replace(updated);
      await _afterChange();
      return PracticeResult(updated);
    } on NetworkException {
      final updated = current.copyWith(
        practiceTimestamps: [
          ...current.practiceTimestamps,
          DateTime.now().toUtc(),
        ],
      );
      _replace(updated);
      _pendingPractices = [..._pendingPractices, dream.id];
      await _store.writePendingPractices(_pendingPractices);
      await _store.writeCache(_dreams);
      notifyListeners();
      return PracticeResult(updated, savedOffline: true);
    }
  }

  Future<Dream> reload(int id) async {
    final dream = await _service.get(id);
    _replace(dream);
    notifyListeners();
    return dream;
  }

  Future<Dream> refreshRehearsal(int id) async {
    final updated = await _service.refreshRehearsal(id);
    _replace(updated);
    notifyListeners();
    return updated;
  }

  Uri rehearsalUri(String link) => _service.audioUri(link);

  Future<Uint8List> downloadRehearsal(String link) =>
      _service.downloadAudio(link);

  Future<Dream> generateRehearsal(
    Dream dream, {
    required String language,
  }) async {
    final updated = await _service.generateRehearsal(
      dream.id,
      language: language,
    );
    _replace(updated);
    notifyListeners();
    return updated;
  }

  /// Pushes drafts and offline practices. Silent on network failure.
  Future<void> syncPending() async {
    if (_syncing || !hasUnsynced) return;
    _syncing = true;
    var changed = false;
    try {
      for (final draft in List.of(_drafts)) {
        try {
          final remote = draft.remoteId;
          final saved = remote == null
              ? await _service.create(draft.input)
              : await _service.update(remote, draft.input);
          var latest = saved;
          for (final _ in draft.practiceTimestamps) {
            latest = await _service.recordPractice(saved.id);
          }
          _replace(latest);
          _drafts = _drafts.where((d) => d.localId != draft.localId).toList();
          changed = true;
        } on NetworkException {
          break;
        } on ApiException {
          _drafts = _drafts.where((d) => d.localId != draft.localId).toList();
          changed = true;
        }
      }
      for (final id in List.of(_pendingPractices)) {
        try {
          _replace(await _service.recordPractice(id));
          _pendingPractices = List.of(_pendingPractices)..remove(id);
          changed = true;
        } on NetworkException {
          break;
        } on ApiException {
          _pendingPractices = List.of(_pendingPractices)..remove(id);
          changed = true;
        }
      }
    } finally {
      _syncing = false;
      await _store.writeDrafts(_drafts);
      await _store.writePendingPractices(_pendingPractices);
      if (changed) await _afterChange();
    }
  }

  Future<void> _ensureFocus() async {
    final candidates = _dreams.where((d) => d.isNightmare && d.hasRewrite);
    if (candidates.isEmpty || candidates.any((d) => d.isCurrentDream)) return;
    final newest = _sorted(candidates.toList()).first;
    try {
      _replace(await _service.setFocus(newest.id));
    } on Exception catch (_) {}
  }

  Future<void> _afterChange() async {
    await _store.writeCache(_dreams);
    notifyListeners();
    onChanged?.call();
  }

  void _replace(Dream dream) {
    final exists = _dreams.any((d) => d.id == dream.id);
    _dreams = exists
        ? [for (final d in _dreams) d.id == dream.id ? dream : d]
        : [dream, ..._dreams];
  }

  Dream? _remote(int? id) =>
      id == null ? null : _dreams.where((d) => d.id == id).firstOrNull;

  static List<Dream> _sorted(List<Dream> dreams) =>
      List.of(dreams)..sort((a, b) {
        final af = a.isCurrentDream && a.hasRewrite;
        final bf = b.isCurrentDream && b.hasRewrite;
        if (af != bf) return af ? -1 : 1;
        return b.createdAt.compareTo(a.createdAt);
      });
}
