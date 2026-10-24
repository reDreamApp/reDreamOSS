import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:path_provider/path_provider.dart';

/// Where the recorder writes: a temporary file on devices.
Future<String> recordingTarget(String fileName) async =>
    '${(await getTemporaryDirectory()).path}/$fileName';

/// The recording at [path]; the file is deleted right away, the narration is
/// sensitive.
Future<Uint8List?> takeRecording(String path) async {
  final file = File(path);
  if (!file.existsSync()) return null;
  final bytes = await file.readAsBytes();
  try {
    file.deleteSync();
  } on FileSystemException {
    // Best effort; the OS clears the temp dir eventually.
  }
  return bytes;
}
