import 'dart:typed_data';

import 'package:redream_app/core/api/api_client.dart';
import 'package:redream_app/features/journal/dream.dart';

/// The backend waits up to 150s for the AI service to synthesize audio.
const rehearsalTimeout = Duration(seconds: 160);

class DreamService {
  DreamService(this._api);

  final ApiClient _api;

  static const _base = '/api/v1/dreams';

  Future<List<Dream>> list() async => [
    for (final e in await _api.getList(_base))
      Dream.fromJson(e as Map<String, dynamic>),
  ];

  Future<Dream> get(int id) async =>
      Dream.fromJson(await _api.get('$_base/$id'));

  Future<Dream> create(DreamInput input) async => Dream.fromJson(
    await _api.post(_base, {...input.toJson(), 'source': 'manual'}),
  );

  Future<Dream> update(int id, DreamInput input) async => Dream.fromJson(
    await _api.put('$_base/$id', input.toJson()..remove('language')),
  );

  Future<void> delete(int id) => _api.delete('$_base/$id');

  Future<Dream> setFocus(int id) async =>
      Dream.fromJson(await _api.patch('$_base/$id/favorite'));

  Future<Dream> recordPractice(int id) async =>
      Dream.fromJson(await _api.post('$_base/$id/practice'));

  Future<Dream> refreshRehearsal(int id) async =>
      Dream.fromJson(await _api.get('$_base/$id/rehearsal-url'));

  Future<Dream> generateRehearsal(int id, {required String language}) async =>
      Dream.fromJson(
        await _api.post('$_base/$id/rehearsal', {
          'language': language,
        }, rehearsalTimeout),
      );

  /// A rehearsal link as the player needs it: the server sends it relative
  /// to its own address.
  Uri audioUri(String link) => _api.resolve(link);

  /// The audio behind a rehearsal link (signed, so fetched without a token).
  Future<Uint8List> downloadAudio(String link) =>
      _api.getBytes(_api.resolve(link));
}
