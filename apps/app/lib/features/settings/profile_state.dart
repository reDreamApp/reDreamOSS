import 'package:flutter/foundation.dart';

import 'package:redream_app/features/settings/profile.dart';
import 'package:redream_app/features/settings/profile_service.dart';

class ProfileState extends ChangeNotifier {
  ProfileState(this._service);

  final ProfileService _service;

  Profile _profile = Profile.empty;
  bool _loading = false;
  bool _loaded = false;
  Object? _error;

  Profile get profile => _profile;
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
      _profile = await _service.fetch();
    } on Exception catch (e) {
      _error = e;
    } finally {
      _loading = false;
      _loaded = true;
      notifyListeners();
    }
  }

  Future<void> save(Profile profile) async {
    _profile = await _service.save(profile);
    notifyListeners();
  }

  Future<void> updateNightmareFrequency(String value) async {
    _profile = await _service.updateNightmareFrequency(value);
    notifyListeners();
  }
}
