import 'dart:async';
import 'dart:js_interop';

import 'package:web/web.dart' as web;

/// Hands the export to the browser as a download; returns the file name.
Future<String> saveExport(String fileName, String json) async {
  final blob = web.Blob(
    [json.toJS].toJS,
    web.BlobPropertyBag(type: 'application/json'),
  );
  final url = web.URL.createObjectURL(blob);
  final link = web.HTMLAnchorElement()
    ..href = url
    ..download = fileName;
  web.document.body!.append(link);
  link
    ..click()
    ..remove();
  // Not right away: some browsers (Safari, older Firefox) cancel a download
  // whose blob is released before it started.
  unawaited(
    Future<void>.delayed(
      const Duration(seconds: 60),
      () => web.URL.revokeObjectURL(url),
    ),
  );
  return fileName;
}
