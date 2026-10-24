import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

/// The web app loads the fonts the engine falls back to from its own server,
/// not from Google (web/flutter_bootstrap.js sets fontFallbackBaseUrl). The
/// engine asks for exact files that a Flutter upgrade can rename: without
/// Roboto the web app shows no text at all, and a missing fallback font is
/// asked for again on every frame.
void main() {
  // flutter_tester runs from <flutter>/bin/cache/artifacts/engine/<platform>/.
  final cache = File(Platform.resolvedExecutable).parent.parent.parent.parent;
  final engine = Directory('${cache.path}/flutter_web_sdk/lib/_engine/engine');
  final skip = engine.existsSync()
      ? false
      : 'the web SDK is not in this Flutter cache (fvm flutter precache --web)';

  String source(String name) => File('${engine.path}/$name').readAsStringSync();

  test('web/fonts has the Roboto file this Flutter version asks for', () {
    final path = RegExp(
      r'roboto/v\d+/[A-Za-z0-9_-]+\.woff2',
    ).firstMatch(source('canvaskit/fonts.dart'))?[0];
    expect(path, isNotNull, reason: 'the engine no longer names Roboto');
    expect(
      File('web/fonts/$path').existsSync(),
      isTrue,
      reason:
          'download https://fonts.gstatic.com/s/$path to web/fonts/$path '
          '(and remove the old file)',
    );
  }, skip: skip);

  test('every font in web/fonts is one this Flutter version asks for', () {
    final named =
        source('canvaskit/fonts.dart') + source('font_fallback_data.dart');
    final shipped = Directory('web/fonts')
        .listSync(recursive: true)
        .whereType<File>()
        .where((f) => f.path.endsWith('.woff2'))
        .map((f) => f.path.substring('web/fonts/'.length))
        .toList();
    expect(shipped, isNotEmpty);
    for (final path in shipped) {
      expect(
        named.contains(path),
        isTrue,
        reason:
            '$path is no longer used: download the file the engine names now '
            'from https://fonts.gstatic.com/s/ instead',
      );
    }
  }, skip: skip);
}
