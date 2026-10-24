import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:web/web.dart' as web;

/// The browser keeps a recording in memory: there is no path.
Future<String> recordingTarget(String fileName) async => '';

/// [path] is the blob: URL the recorder returns in a browser; it is released
/// once read.
Future<Uint8List?> takeRecording(String path) async {
  try {
    final res = await http.get(Uri.parse(path));
    return res.statusCode == 200 ? res.bodyBytes : null;
  } finally {
    web.URL.revokeObjectURL(path);
  }
}
