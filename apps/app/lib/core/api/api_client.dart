import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

import 'package:redream_app/core/api/api_exception.dart';

typedef TokenProvider = String? Function();

/// Told which token a request carried when it got a 401; true to retry once
/// with the token held now.
typedef UnauthorizedHandler = Future<bool> Function(String? usedToken);

/// Calls that wait on the LLM: the backend allows the AI service 90s.
const aiTimeout = Duration(seconds: 100);

/// Files the server links to (rehearsal audio, a few MB).
const downloadTimeout = Duration(seconds: 120);

/// JSON client for `apps/server`. Reads the server's address on every
/// request, adds the bearer token, maps transport failures to
/// [NetworkException], non-2xx to [ApiException], and on a 401 asks
/// [UnauthorizedHandler] whether to retry once.
class ApiClient {
  ApiClient({
    required String Function() baseUrl,
    required TokenProvider tokenProvider,
    UnauthorizedHandler? onUnauthorized,
    http.Client? httpClient,
    Duration timeout = const Duration(seconds: 30),
  }) : _baseUrl = baseUrl,
       _tokenProvider = tokenProvider,
       _onUnauthorized = onUnauthorized,
       _http = httpClient ?? http.Client(),
       _timeout = timeout;

  /// A 401 here is a wrong password, not an ended session.
  static const _signInPaths = {
    '/api/v1/auth/login',
    '/api/v1/auth/register',
    '/api/v1/auth/personal',
  };

  final String Function() _baseUrl;
  final TokenProvider _tokenProvider;
  final UnauthorizedHandler? _onUnauthorized;
  final http.Client _http;
  final Duration _timeout;

  Future<Map<String, dynamic>> get(String path, {Map<String, String>? query}) =>
      _map(_request('GET', path, query: query));

  Future<List<dynamic>> getList(String path, {Map<String, String>? query}) =>
      _list(_request('GET', path, query: query));

  /// [timeout] overrides the default for calls that wait on the LLM.
  Future<Map<String, dynamic>> post(
    String path, [
    Object? body,
    Duration? timeout,
  ]) => _map(_request('POST', path, body: body, timeout: timeout));

  /// A POST without a body that carries [headers] as well (the personal
  /// server's key).
  Future<Map<String, dynamic>> postWithHeaders(
    String path,
    Map<String, String> headers,
  ) => _map(_request('POST', path, headers: headers));

  Future<Map<String, dynamic>> put(String path, Object? body) =>
      _map(_request('PUT', path, body: body));

  Future<Map<String, dynamic>> patch(String path, [Object? body]) =>
      _map(_request('PATCH', path, body: body));

  Future<void> delete(String path) => _request('DELETE', path);

  /// A link the server sent, made absolute. A server-relative link is
  /// joined to the address as text, like every request: [Uri.resolve] would
  /// drop a sub-path such as `https://host/dreams`. Absolute http(s) links
  /// stay as they are, except an http link from an https server.
  Uri resolve(String link) {
    final uri = Uri.tryParse(link);
    if (uri != null &&
        (uri.isScheme('http') || uri.isScheme('https')) &&
        uri.host.isNotEmpty) {
      if (uri.isScheme('http') && _uri('/', null).isScheme('https')) {
        throw NetworkException('insecure link');
      }
      return uri;
    }
    return _uri(link, null);
  }

  /// Downloads a file the server linked to. No token is sent: those links
  /// are signed and work without one, and an absolute link may point
  /// elsewhere. Redirects are not followed, so https stays https.
  Future<Uint8List> getBytes(Uri url) async {
    final request = http.Request('GET', url)..followRedirects = false;
    final res = await _guard(
      () => _http
          .send(request)
          .then(http.Response.fromStream)
          .timeout(downloadTimeout),
    );
    if (res.statusCode < 200 || res.statusCode >= 300) {
      throw ApiException(res.statusCode, _errorMessage(res.body));
    }
    return res.bodyBytes;
  }

  /// POSTs [body] to a `text/event-stream` endpoint and yields the response
  /// as utf8 text chunks. Errors before the stream starts surface as the same
  /// [ApiException] / [NetworkException] the JSON calls throw.
  Stream<String> postStream(String path, Object? body) async* {
    final res = await _authorized(
      path,
      (token) {
        final request = http.Request('POST', _uri(path, null))
          ..headers.addAll(_headers(token, json: true))
          ..headers['Accept'] = 'text/event-stream';
        if (body != null) request.body = jsonEncode(body);
        return _guard(() => _http.send(request).timeout(aiTimeout));
      },
      discard: (res) => res.stream.drain<void>(),
    );
    if (res.statusCode < 200 || res.statusCode >= 300) {
      _decode(await http.Response.fromStream(res));
    }
    yield* res.stream.transform(utf8.decoder);
  }

  /// POSTs [bytes] as a multipart upload in the form field `file`.
  Future<Map<String, dynamic>> postMultipart(
    String path, {
    required List<int> bytes,
    required String fileName,
  }) async {
    final res = await _authorized(
      path,
      (token) {
        final request = http.MultipartRequest('POST', _uri(path, null))
          ..headers.addAll(_headers(token))
          ..files.add(
            http.MultipartFile.fromBytes('file', bytes, filename: fileName),
          );
        return _guard(
          () async => http.Response.fromStream(
            await _http.send(request).timeout(aiTimeout),
          ),
        );
      },
    );
    return _map(Future.value(_decode(res)));
  }

  Future<Object?> _request(
    String method,
    String path, {
    Object? body,
    Map<String, String>? query,
    Duration? timeout,
    Map<String, String>? headers,
  }) async {
    // The address is read with the token, also on the retry: both belong
    // to the server in use at that moment.
    final res = await _authorized(
      path,
      (token) => _send(
        method,
        _uri(path, query),
        body,
        timeout ?? _timeout,
        token,
        headers,
      ),
    );
    return _decode(res);
  }

  /// Sends with the current token. On a 401 the handler hears which token
  /// was used; a retry happens at most once, and only when it says so. A 401
  /// on the retry is reported too, so that session ends as well.
  Future<R> _authorized<R extends http.BaseResponse>(
    String path,
    Future<R> Function(String? token) send, {
    Future<void> Function(R)? discard,
  }) async {
    final handler = _onUnauthorized;
    final token = _tokenProvider();
    final res = await send(token);
    if (res.statusCode != 401 ||
        handler == null ||
        _signInPaths.contains(path.startsWith('/') ? path : '/$path') ||
        !await handler(token)) {
      return res;
    }
    await discard?.call(res);
    final retryToken = _tokenProvider();
    final retried = await send(retryToken);
    if (retried.statusCode == 401) await handler(retryToken);
    return retried;
  }

  /// Without an address there is no server to reach: that fails like being
  /// offline. A query already in [path] is kept when [query] is null.
  Uri _uri(String path, Map<String, String>? query) {
    final base = _baseUrl().replaceAll(RegExp(r'/+$'), '');
    if (base.isEmpty) throw NetworkException('no server address');
    return Uri.parse(
      '$base${path.startsWith('/') ? '' : '/'}$path',
    ).replace(queryParameters: query == null || query.isEmpty ? null : query);
  }

  Map<String, String> _headers(String? token, {bool json = false}) => {
    'Accept': 'application/json',
    if (json) 'Content-Type': 'application/json',
    if (token != null && token.isNotEmpty) 'Authorization': 'Bearer $token',
  };

  /// Maps transport failures to [NetworkException].
  static Future<T> _guard<T>(Future<T> Function() call) async {
    try {
      return await call();
    } on http.ClientException catch (e) {
      throw NetworkException(e.message);
    } on SocketException catch (e) {
      throw NetworkException(e.message);
    } on TimeoutException {
      throw NetworkException('timeout');
    }
  }

  Future<http.Response> _send(
    String method,
    Uri uri,
    Object? body,
    Duration timeout,
    String? token, [
    Map<String, String>? extra,
  ]) async {
    final headers = {..._headers(token, json: body != null), ...?extra};
    final encoded = body == null ? null : jsonEncode(body);
    return _guard(() {
      final future = switch (method) {
        'GET' => _http.get(uri, headers: headers),
        'POST' => _http.post(uri, headers: headers, body: encoded),
        'PUT' => _http.put(uri, headers: headers, body: encoded),
        'PATCH' => _http.patch(uri, headers: headers, body: encoded),
        'DELETE' => _http.delete(uri, headers: headers),
        _ => throw ArgumentError.value(method, 'method'),
      };
      return future.timeout(timeout);
    });
  }

  Object? _decode(http.Response res) {
    if (res.statusCode < 200 || res.statusCode >= 300) {
      throw ApiException(
        res.statusCode,
        _errorMessage(res.body),
        retryAfterSeconds: int.tryParse(res.headers['retry-after'] ?? ''),
      );
    }
    if (res.body.isEmpty) return null;
    return jsonDecode(utf8.decode(res.bodyBytes));
  }

  Future<Map<String, dynamic>> _map(Future<Object?> pending) async {
    final decoded = await pending;
    if (decoded == null) return <String, dynamic>{};
    if (decoded is Map<String, dynamic>) return decoded;
    throw ApiException(200, 'Expected object, got ${decoded.runtimeType}');
  }

  Future<List<dynamic>> _list(Future<Object?> pending) async {
    final decoded = await pending;
    if (decoded is List) return decoded;
    throw ApiException(200, 'Expected list, got ${decoded.runtimeType}');
  }

  /// FastAPI puts the human-readable error under `detail`, either a string
  /// or (for validation errors) a list of `{loc, msg}` entries.
  static String _errorMessage(String body) {
    final Object? decoded;
    try {
      decoded = jsonDecode(body);
    } on FormatException {
      return body;
    }
    final detail = decoded is Map<String, dynamic> ? decoded['detail'] : null;
    if (detail is String) return detail;
    if (detail is List) {
      final messages = detail
          .whereType<Map<String, dynamic>>()
          .map((e) => e['msg'])
          .whereType<String>()
          .join('; ');
      if (messages.isNotEmpty) return messages;
    }
    return body;
  }
}
