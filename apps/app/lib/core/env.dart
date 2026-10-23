import 'package:flutter_dotenv/flutter_dotenv.dart';

class Env {
  static String get apiBaseUrl => _required('API_BASE_URL');

  static String get blogMetaUrl =>
      _optional('BLOG_META_URL') ??
      'https://www.redreamai.com/content/blog/posts/meta.json';

  static Future<void> load() => dotenv.load();

  static String _required(String key) {
    final value = _optional(key);
    if (value == null) throw StateError('Missing required env var: $key');
    return value;
  }

  static String? _optional(String key) {
    final value = dotenv.env[key]?.trim();
    return value == null || value.isEmpty ? null : value;
  }
}
