import 'dart:io';

import 'package:path_provider/path_provider.dart';

/// Saves the export in the app's documents folder; returns where it is.
Future<String> saveExport(String fileName, String json) async {
  final dir = await getApplicationDocumentsDirectory();
  final file = File('${dir.path}/$fileName');
  await file.writeAsString(json, flush: true);
  return file.path;
}
