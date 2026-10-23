import 'package:flutter/foundation.dart';

import 'package:redream_app/features/home/progress_models.dart';
import 'package:redream_app/features/home/progress_service.dart';

class ProgressState extends ChangeNotifier {
  ProgressState(this._service);

  final ProgressService _service;

  ProgressStats? _stats;
  bool _loading = false;
  bool _loaded = false;
  Object? _error;

  ProgressStats? get stats => _stats;
  bool get loading => _loading;
  bool get loaded => _loaded;
  Object? get error => _error;

  Future<void> load() async {
    if (_loaded || _loading) return;
    await refresh();
  }

  Future<void> refresh() async {
    _loading = true;
    _error = null;
    notifyListeners();
    try {
      _stats = await _service.stats();
    } on Exception catch (e) {
      _error = e;
    } finally {
      _loading = false;
      _loaded = true;
      notifyListeners();
    }
  }

  /// Saves today's mood, then refreshes the averages. Throws on failure.
  Future<void> setTodayMood(Mood mood) async {
    await _service.setTodayMood(mood);
    await refresh();
  }
}
