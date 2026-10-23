import 'dart:async';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:redream_app/core/server/server_config.dart';
import 'package:redream_app/core/storage/local_store.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../../fake_api.dart';

const _consent = '2026-10-07';

Matcher _fails(ServerFailureKind kind) =>
    throwsA(isA<ServerFailure>().having((e) => e.kind, 'kind', kind));

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late LocalStore store;
  late FakeApi api;

  setUp(() async {
    FlutterSecureStorage.setMockInitialValues({});
    SharedPreferences.setMockInitialValues({});
    store = await LocalStore.open();
    api = FakeApi()..on('GET /api/v1/instance', (_, _) => instanceJson());
  });

  ServerConfig config({
    String defaultUrl = '',
    bool httpsOnly = false,
    http.Client? client,
  }) => ServerConfig(
    store,
    consentVersion: _consent,
    httpClient: client ?? api.httpClient(),
    defaultUrl: defaultUrl,
    httpsOnly: httpsOnly,
  );

  group('normalize', () {
    test('one form: https by default, lower-case host, no trailing slash', () {
      // Tests run as a debug build: http is allowed.
      String n(String input) => ServerConfig.normalize(input);

      expect(n('dreams.example.org'), 'https://dreams.example.org');
      expect(
        n(' HTTPS://Dreams.Example.org:8443/base/ '),
        'https://dreams.example.org:8443/base',
      );
      expect(
        n('https://dreams.example.org:443/'),
        'https://dreams.example.org',
      );
      expect(n('http://10.0.2.2:8001'), 'http://10.0.2.2:8001');
      expect(n('http://[::1]:8001'), 'http://[::1]:8001');
    });

    test('anything but a plain http(s) address is refused', () {
      for (final input in [
        '',
        '   ',
        'dreams example.org',
        'ftp://dreams.example.org',
        'https://',
        'https://user:pw@dreams.example.org',
        'https://dreams.example.org/?a=1',
        'https://dreams.example.org/#top',
      ]) {
        expect(
          () => ServerConfig.normalize(input),
          _fails(ServerFailureKind.invalidAddress),
          reason: input,
        );
      }
    });

    test('release builds take https only', () {
      expect(
        () => ServerConfig.normalize('http://dreams.example.org'),
        // Tests run in debug mode; the release rule is the flag.
        returnsNormally,
      );
      expect(
        () => ServerConfig.normalize('http://10.0.2.2:8001', httpsOnly: true),
        _fails(ServerFailureKind.httpsRequired),
      );
      expect(
        ServerConfig.normalize('dreams.example.org', httpsOnly: true),
        'https://dreams.example.org',
      );
    });
  });

  group('connect', () {
    test(
      'keeps the address and what the server said, for offline starts',
      () async {
        final server = config();
        var notified = 0;
        server.addListener(() => notified++);

        await server.connect('http://fake/');

        expect(server.isConnected, isTrue);
        expect(server.baseUrl, 'http://fake');
        expect(server.address, 'http://fake');
        expect(server.url.value, 'http://fake');
        expect(server.instance!.inviteOnly, isTrue);
        expect(notified, 1);

        // The next start reads both without asking the server.
        api.offline = true;
        final again = config();
        expect(again.isConnected, isTrue);
        expect(again.baseUrl, 'http://fake');
        expect(again.url.value, 'http://fake');
        expect(again.instance!.rehearsalAudio, isTrue);
      },
    );

    test('asks without a token and does not follow redirects', () async {
      final sent = <http.BaseRequest>[];
      final server = config(
        client: MockClient((request) async {
          sent.add(request);
          return http.Response('', 302, headers: {'location': 'http://evil'});
        }),
      );

      await expectLater(
        server.connect('https://dreams.example.org'),
        _fails(ServerFailureKind.notAServer),
      );
      expect(
        sent.single.url.toString(),
        'https://dreams.example.org/api/v1/instance',
      );
      expect(sent.single.followRedirects, isFalse);
      expect(sent.single.headers.containsKey('Authorization'), isFalse);
      expect(server.isConnected, isFalse);
    });

    test('says why a server is refused, and keeps nothing', () async {
      Future<void> refused(Object? answer, ServerFailureKind kind) async {
        api.on('GET /api/v1/instance', (_, _) {
          if (answer is Exception) throw answer;
          return answer;
        });
        final server = config();
        await expectLater(server.connect('http://fake'), _fails(kind));
        expect(server.isConnected, isFalse);
        expect(config().isConnected, isFalse);
      }

      await refused(
        FakeApiError(404, 'not found'),
        ServerFailureKind.notAServer,
      );
      await refused(
        FakeApiError(502, 'bad gateway'),
        ServerFailureKind.unreachable,
      );
      await refused({'hello': 'world'}, ServerFailureKind.notAServer);
      await refused(
        instanceJson(apiVersion: 2),
        ServerFailureKind.appOutdated,
      );
      await refused(
        instanceJson(consentVersion: '2027-01-01'),
        ServerFailureKind.appOutdated,
      );
      await refused(
        instanceJson(apiVersion: 0),
        ServerFailureKind.serverOutdated,
      );
      await refused(
        instanceJson(consentVersion: '2026-01-01'),
        ServerFailureKind.serverOutdated,
      );
    });

    test('a page that is not JSON is not a server', () async {
      final server = config(
        client: MockClient((_) async => http.Response('<html></html>', 200)),
      );
      await expectLater(
        server.connect('https://example.org'),
        _fails(ServerFailureKind.notAServer),
      );
    });

    test('offline or refused connections are unreachable', () async {
      api.offline = true;
      await expectLater(
        config().connect('http://fake'),
        _fails(ServerFailureKind.unreachable),
      );
    });
  });

  group('refresh', () {
    test('takes the new answer, keeps the last one offline', () async {
      final server = config();
      await server.connect('http://fake');

      api.on(
        'GET /api/v1/instance',
        (_, _) => instanceJson(registration: 'open', voice: false),
      );
      await server.refresh();
      expect(server.instance!.registration, 'open');
      expect(server.instance!.transcription, isFalse);
      expect(config().instance!.registration, 'open');

      api.offline = true;
      await server.refresh();
      expect(server.instance!.registration, 'open');
    });

    test(
      'keeps the last answer when the server moved to another API',
      () async {
        final server = config();
        await server.connect('http://fake');
        api.on(
          'GET /api/v1/instance',
          (_, _) => instanceJson(apiVersion: 2, registration: 'closed'),
        );

        await server.refresh();

        expect(server.instance!.apiVersion, 1);
        expect(server.instance!.registrationClosed, isFalse);
      },
    );

    test('an answer that comes after a disconnect is dropped', () async {
      final server = config();
      await server.connect('http://fake');
      final answer = Completer<void>();
      api.delay = answer.future;

      final refresh = server.refresh();
      await server.disconnect();
      answer.complete();
      await refresh;

      expect(server.isConnected, isFalse);
      expect(server.instance, isNull);
    });
  });

  test('disconnect forgets the server and offers its address again', () async {
    final server = config();
    await server.connect('http://fake');
    var notified = 0;
    server.addListener(() => notified++);

    await server.disconnect();

    expect(server.isConnected, isFalse);
    expect(server.baseUrl, isNull);
    expect(server.instance, isNull);
    expect(server.url.value, '');
    expect(server.prefill, 'http://fake');
    expect(notified, 1);
    expect(config().isConnected, isFalse);
  });

  group('a preset address', () {
    test('is tried once on a first start, not after a disconnect', () async {
      final server = config(defaultUrl: 'http://fake');
      expect(server.prefill, 'http://fake');
      expect(server.takeAutoConnect(), isTrue);
      expect(server.takeAutoConnect(), isFalse);

      await server.connect(server.prefill);
      await server.disconnect();
      expect(config(defaultUrl: 'http://fake').takeAutoConnect(), isFalse);
    });

    test('is not tried when an address was kept', () async {
      await config().connect('http://fake');
      final server = config(defaultUrl: 'https://preset.example');
      expect(server.takeAutoConnect(), isFalse);
      expect(server.baseUrl, 'http://fake');
    });
  });

  test(
    'a kept http address does not carry over into a release build',
    () async {
      await config().connect('http://fake');

      final release = config(httpsOnly: true);

      expect(release.isConnected, isFalse);
      expect(release.prefill, 'http://fake');
    },
  );
}
