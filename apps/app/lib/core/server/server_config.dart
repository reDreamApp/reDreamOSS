import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;

import 'package:redream_app/core/server/instance_info.dart';
import 'package:redream_app/core/storage/local_store.dart';

/// The HTTP API version this app speaks; a server must report the same.
const appApiVersion = 1;

enum ServerFailureKind {
  invalidAddress,
  httpsRequired,
  unreachable,
  notAServer,
  appOutdated,
  serverOutdated,
}

/// A server address that was refused, already told apart for the UI.
class ServerFailure implements Exception {
  const ServerFailure(this.kind);

  final ServerFailureKind kind;

  @override
  String toString() => 'ServerFailure(${kind.name})';
}

/// The server this app talks to: chosen once on the address screen (or
/// preset with `--dart-define=DEFAULT_SERVER_URL=...`) and kept in
/// [LocalStore] with its last [InstanceInfo], so the app also starts
/// offline. Release builds accept https only, except on this computer
/// (localhost); dart:io follows neither Android's cleartext rule nor iOS's
/// ATS, so it is checked here.
///
/// The web app is served by its server: `pageOrigin` (the page's own origin)
/// is then the one address, connected on every start, never changed.
class ServerConfig extends ChangeNotifier {
  ServerConfig(
    this._store, {
    required String consentVersion,
    http.Client? httpClient,
    String defaultUrl = const String.fromEnvironment('DEFAULT_SERVER_URL'),
    String? pageOrigin,
    bool httpsOnly = kReleaseMode,
  }) : _consentVersion = consentVersion,
       _http = httpClient ?? http.Client(),
       _defaultUrl = (pageOrigin ?? defaultUrl).trim(),
       _fixed = pageOrigin != null,
       _httpsOnly = httpsOnly {
    final stored = _store.getString(_urlKey);
    final last = _store.getString(_lastKey);
    var url = stored == null ? null : _validOrNull(stored);
    if (_fixed && url != _validOrNull(_defaultUrl)) url = null;
    final info = _storedInfo();
    if (url != null && info != null) {
      _set(url, info, notify: false);
    } else {
      _prefill = _fixed ? null : stored ?? last;
    }
    // Someone who left a server chooses the next one themselves.
    _autoConnect =
        _fixed || (stored == null && last == null && _defaultUrl.isNotEmpty);
  }

  static const _urlKey = 'server_url';
  static const _infoKey = 'server_instance';
  static const _lastKey = 'server_last_url';
  static const _probeTimeout = Duration(seconds: 10);

  final LocalStore _store;
  final String _consentVersion;
  final http.Client _http;
  final String _defaultUrl;
  final bool _fixed;
  final bool _httpsOnly;
  final _url = ValueNotifier<String>('');

  String? _baseUrl;
  InstanceInfo? _instance;
  String? _prefill;
  bool _autoConnect = false;

  /// The server's address without a trailing slash; null until one is
  /// connected. Requests read it each time, so a change applies at once.
  String? get baseUrl => _baseUrl;

  /// What the server said about itself when last asked; set whenever
  /// [baseUrl] is.
  InstanceInfo? get instance => _instance;
  bool get isConnected => _baseUrl != null;

  /// The web app's address is the page's own: there is no other server to
  /// change to.
  bool get fixedAddress => _fixed;

  /// [baseUrl] as a listenable ('' without one): a session belongs to one
  /// server address.
  ValueListenable<String> get url => _url;

  /// [baseUrl] as people read it: https:// is left out, http:// is not.
  String get address => (_baseUrl ?? '').replaceFirst('https://', '');

  /// What the address field starts with: the last address (kept across
  /// restarts), else the preset.
  String get prefill => _prefill ?? _defaultUrl;

  /// True once, on a first start with a preset address, so that address is
  /// tried without asking; never after a server was left.
  bool takeAutoConnect() {
    final take = _autoConnect;
    _autoConnect = _fixed;
    return take;
  }

  /// Checks [input], asks the server about itself and keeps it when it
  /// speaks this app's API and consent version. Throws [ServerFailure].
  Future<void> connect(String input) async {
    final url = normalize(input, httpsOnly: _httpsOnly);
    final info = await _probe(url);
    if (_problem(info) case final problem?) throw ServerFailure(problem);
    await _store.setString(_urlKey, url);
    await _store.setJson(_infoKey, info.toJson());
    await _store.remove(_lastKey);
    _autoConnect = _fixed;
    _set(url, info);
  }

  /// Asks the connected server again (features or texts can change with a
  /// restart). Offline or with a server on another API version the last
  /// answer stays. A changed consent version is taken as it is: the consent
  /// gate reads the server's own consent status and says when the app needs
  /// an update.
  Future<void> refresh() async {
    final url = _baseUrl;
    if (url == null) return;
    final InstanceInfo info;
    try {
      info = await _probe(url);
    } on ServerFailure catch (_) {
      return;
    }
    if (_baseUrl != url || info.apiVersion != appApiVersion) return;
    // Resumes are frequent (permission dialogs, the notification shade):
    // nothing is rebuilt when nothing changed.
    final json = jsonEncode(info.toJson());
    if (json == jsonEncode(_instance?.toJson())) return;
    await _store.setJson(_infoKey, info.toJson());
    if (_baseUrl != url) return;
    _instance = info;
    notifyListeners();
  }

  /// Forgets the server. Sign out first: a session ends with its server.
  Future<void> disconnect() async {
    final url = _baseUrl;
    if (url == null) return;
    _prefill = url;
    _autoConnect = _fixed;
    _set(null, null);
    await _store.setString(_lastKey, url);
    await _store.remove(_urlKey);
    await _store.remove(_infoKey);
  }

  /// The address in one form: scheme (https when none is typed), host,
  /// optional port and path, no trailing slash.
  static String normalize(String input, {bool httpsOnly = kReleaseMode}) {
    const invalid = ServerFailure(ServerFailureKind.invalidAddress);
    final text = input.trim();
    if (text.isEmpty || text.contains(RegExp(r'\s'))) throw invalid;
    final uri = Uri.tryParse(text.contains('://') ? text : 'https://$text');
    if (uri == null ||
        !(uri.isScheme('http') || uri.isScheme('https')) ||
        uri.host.isEmpty ||
        uri.host.contains('%') ||
        uri.userInfo.isNotEmpty ||
        uri.hasQuery ||
        uri.hasFragment) {
      throw invalid;
    }
    if (httpsOnly && !uri.isScheme('https') && !isLoopback(uri.host)) {
      throw const ServerFailure(ServerFailureKind.httpsRequired);
    }
    return Uri(
      scheme: uri.scheme,
      host: uri.host,
      port: uri.hasPort ? uri.port : null,
      path: uri.path.replaceAll(RegExp(r'/+$'), ''),
    ).toString();
  }

  /// This computer: plain http never leaves it.
  static bool isLoopback(String host) =>
      host == 'localhost' ||
      host == '::1' ||
      RegExp(r'^127(\.\d{1,3}){3}$').hasMatch(host);

  /// `GET /api/v1/instance` without a token. Redirects are not followed: a
  /// server must answer at the address people typed.
  Future<InstanceInfo> _probe(String url) async {
    final request = http.Request('GET', Uri.parse('$url/api/v1/instance'))
      ..followRedirects = false
      ..headers['Accept'] = 'application/json';
    final http.Response res;
    try {
      res = await _http
          .send(request)
          .then(http.Response.fromStream)
          .timeout(_probeTimeout);
      // DNS, refused connections, TLS errors and timeouts alike.
    } on Exception catch (_) {
      throw const ServerFailure(ServerFailureKind.unreachable);
    }
    // A proxy in front of a server that is restarting answers 502/503.
    if (res.statusCode >= 500) {
      throw const ServerFailure(ServerFailureKind.unreachable);
    }
    if (res.statusCode != 200) {
      throw const ServerFailure(ServerFailureKind.notAServer);
    }
    try {
      return InstanceInfo.fromJson(jsonDecode(utf8.decode(res.bodyBytes)));
    } on FormatException catch (_) {
      throw const ServerFailure(ServerFailureKind.notAServer);
    }
  }

  /// Consent versions are dates (YYYY-MM-DD), so they compare as text.
  ServerFailureKind? _problem(InstanceInfo info) {
    final consent = info.consentVersion.compareTo(_consentVersion);
    if (info.apiVersion > appApiVersion || consent > 0) {
      return ServerFailureKind.appOutdated;
    }
    if (info.apiVersion < appApiVersion || consent < 0) {
      return ServerFailureKind.serverOutdated;
    }
    return null;
  }

  /// A stored address is checked again: a debug build's http address does
  /// not carry over into a release build.
  String? _validOrNull(String url) {
    try {
      return normalize(url, httpsOnly: _httpsOnly);
    } on ServerFailure catch (_) {
      return null;
    }
  }

  InstanceInfo? _storedInfo() {
    try {
      return InstanceInfo.fromJson(_store.getJson(_infoKey));
    } on FormatException catch (_) {
      return null;
    }
  }

  void _set(String? url, InstanceInfo? info, {bool notify = true}) {
    _baseUrl = url;
    _instance = info;
    _url.value = url ?? '';
    if (notify) notifyListeners();
  }

  @override
  void dispose() {
    _url.dispose();
    super.dispose();
  }
}
