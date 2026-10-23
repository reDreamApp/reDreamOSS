import 'dart:collection';

/// Values for `FlutterSecureStorage.setMockInitialValues` that lose every
/// write, like a keychain that is not available.
class DroppedWrites extends MapBase<String, String> {
  final _data = <String, String>{};

  @override
  String? operator [](Object? key) => _data[key];

  @override
  void operator []=(String key, String value) {}

  @override
  void clear() => _data.clear();

  @override
  Iterable<String> get keys => _data.keys;

  @override
  String? remove(Object? key) => _data.remove(key);
}
