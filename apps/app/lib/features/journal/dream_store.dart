import 'package:redream_app/core/storage/local_store.dart';
import 'package:redream_app/features/journal/dream.dart';

/// Per-user offline state: the last server list, unsent drafts and practices
/// recorded while offline.
class DreamStore {
  DreamStore(this._store, this.userId);

  final LocalStore _store;
  final String userId;

  String get _cacheKey => 'dreams_cache:$userId';
  String get _draftsKey => 'dream_drafts:$userId';
  String get _practicesKey => 'pending_practices:$userId';

  List<Dream> readCache() => _readList(_cacheKey, Dream.fromJson);
  Future<void> writeCache(List<Dream> dreams) =>
      _store.setJson(_cacheKey, [for (final d in dreams) d.toJson()]);

  List<DreamDraft> readDrafts() => _readList(_draftsKey, DreamDraft.fromJson);
  Future<void> writeDrafts(List<DreamDraft> drafts) =>
      _store.setJson(_draftsKey, [for (final d in drafts) d.toJson()]);

  /// Dream ids with a practice recorded offline (one entry per session).
  List<int> readPendingPractices() => [
    if (_store.getJson(_practicesKey) case final List<dynamic> list)
      for (final e in list)
        if (e is int) e,
  ];
  Future<void> writePendingPractices(List<int> dreamIds) =>
      _store.setJson(_practicesKey, dreamIds);

  List<T> _readList<T>(String key, T Function(Map<String, dynamic>) parse) => [
    if (_store.getJson(key) case final List<dynamic> list)
      for (final e in list)
        if (e is Map<String, dynamic>) parse(e),
  ];
}
