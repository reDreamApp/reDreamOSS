import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:redream_app/core/api/api_exception.dart';

import 'fake_api.dart';

void main() {
  test('sends bearer token and decodes JSON objects and lists', () async {
    final api = FakeApi()
      ..on('GET /api/v1/one', (_, _) => {'a': 1})
      ..on('GET /api/v1/many', (_, _) => [1, 2]);
    final client = api.client(token: 't0k');

    expect(await client.get('/api/v1/one'), {'a': 1});
    expect(await client.getList('api/v1/many'), [1, 2]);
    expect(api.lastToken, 'Bearer t0k');
  });

  test('maps transport failures to NetworkException', () async {
    final client = FakeApi(offline: true).client();
    await expectLater(client.get('/x'), throwsA(isA<NetworkException>()));
  });

  test(
    'maps non-2xx to ApiException with FastAPI detail and Retry-After',
    () async {
      final api = FakeApi()
        ..on(
          'POST /api/v1/limited',
          (_, _) => throw FakeApiError(
            429,
            'budget exceeded',
            headers: {'retry-after': '120'},
          ),
        );
      try {
        await api.client().post('/api/v1/limited', {'x': 1});
        fail('expected ApiException');
      } on ApiException catch (e) {
        expect(e.isRateLimited, isTrue);
        expect(e.message, 'budget exceeded');
        expect(e.retryAfterSeconds, 120);
      }
    },
  );

  group('401 hook', () {
    late FakeApi api;
    late String token;
    late List<String?> hookCalls;
    late List<String?> sentTokens;

    /// Like TokenAuthService for one user: retry only when another token is
    /// held now.
    Future<bool> hook(String? used) async {
      hookCalls.add(used);
      return token != used;
    }

    setUp(() {
      token = 'old';
      hookCalls = [];
      sentTokens = [];
      api = FakeApi();
    });

    Object? reject(http.Request request, [String? signInAs]) {
      sentTokens.add(request.headers['Authorization']);
      if (signInAs != null) token = signInAs;
      throw FakeApiError(401, 'not authenticated');
    }

    test('retries once with the token held now', () async {
      api.on('GET /api/v1/me', (request, _) {
        if (request.headers['Authorization'] == 'Bearer new') {
          sentTokens.add('Bearer new');
          return {'ok': true};
        }
        // The user signed in again while this request was under way.
        return reject(request, 'new');
      });
      final client = api.client(
        tokenProvider: () => token,
        onUnauthorized: hook,
      );

      expect(await client.get('/api/v1/me'), {'ok': true});
      expect(hookCalls, ['old']);
      expect(sentTokens, ['Bearer old', 'Bearer new']);
    });

    test('does not retry when the token is unchanged', () async {
      api.on('GET /api/v1/me', (request, _) => reject(request));
      final client = api.client(
        tokenProvider: () => token,
        onUnauthorized: hook,
      );

      await expectLater(
        client.get('/api/v1/me'),
        throwsA(isA<ApiException>().having((e) => e.statusCode, 'status', 401)),
      );
      expect(hookCalls, ['old']);
      expect(sentTokens, ['Bearer old']);
    });

    test('retries at most once', () async {
      api.on('POST /api/v1/me', (request, _) => reject(request));
      final client = api.client(
        tokenProvider: () => token,
        onUnauthorized: (used) async {
          hookCalls.add(used);
          return true;
        },
      );

      await expectLater(
        client.post('/api/v1/me', {'x': 1}),
        throwsA(isA<ApiException>()),
      );
      // The refused retry is reported, but not sent a third time.
      expect(hookCalls, ['old', 'old']);
      expect(sentTokens, hasLength(2));
    });

    test('a refused retry reports its own token', () async {
      api.on(
        'GET /api/v1/me',
        (request, _) => reject(
          request,
          request.headers['Authorization'] == 'Bearer old' ? 'new' : null,
        ),
      );
      final client = api.client(
        tokenProvider: () => token,
        onUnauthorized: hook,
      );

      await expectLater(
        client.get('/api/v1/me'),
        throwsA(isA<ApiException>().having((e) => e.statusCode, 'status', 401)),
      );
      expect(hookCalls, ['old', 'new']);
      expect(sentTokens, ['Bearer old', 'Bearer new']);
    });

    test('a streamed request retries with the new token too', () async {
      api.on('POST /api/v1/chats/1/message/stream', (request, _) {
        if (request.headers['Authorization'] == 'Bearer new') {
          return SseBody([
            {'content': 'hi'},
          ]);
        }
        return reject(request, 'new');
      });
      final client = api.client(
        tokenProvider: () => token,
        onUnauthorized: hook,
      );

      final text = await client.postStream('/api/v1/chats/1/message/stream', {
        'content': 'x',
      }).join();
      expect(text, contains('hi'));
      expect(hookCalls, ['old']);
    });

    test('a 401 from login or register never reaches the hook', () async {
      api
        ..on('POST /api/v1/auth/login', (request, _) => reject(request, 'x'))
        ..on(
          'POST /api/v1/auth/register',
          (request, _) => reject(request, 'x'),
        );
      final client = api.client(
        tokenProvider: () => token,
        onUnauthorized: hook,
      );

      for (final path in ['/api/v1/auth/login', 'api/v1/auth/register']) {
        await expectLater(
          client.post(path, {'email': 'a@b.cd', 'password': 'wrong-one'}),
          throwsA(isA<ApiException>()),
        );
      }
      expect(hookCalls, isEmpty);
      expect(sentTokens, hasLength(2));
    });
  });

  test('delete tolerates empty 204 bodies', () async {
    final api = FakeApi()..on('DELETE /api/v1/thing/1', (_, _) => null);
    await api.client().delete('/api/v1/thing/1');
    expect(api.calls, ['DELETE /api/v1/thing/1']);
  });
}
