import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:redream_app/core/api/api_client.dart';
import 'package:redream_app/core/api/api_exception.dart';
import 'package:redream_app/core/auth/auth_service.dart';
import 'package:redream_app/core/auth/session_store.dart';
import 'package:redream_app/core/auth/token_auth_service.dart';

import '../../fake_api.dart';
import 'dropped_writes.dart';

const _server = 'http://fake';

Map<String, Object?> _authJson(
  String token, {
  String email = 'me@example.com',
}) => {
  'token': token,
  'expires_at': '2026-11-22T10:00:00Z',
  'user': {'id': 'user-1', 'email': email},
};

StoredSession _stored({String token = 'rdo_old', String server = _server}) =>
    StoredSession(
      token: token,
      userId: 'user-1',
      email: 'me@example.com',
      server: server,
    );

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late FakeApi api;
  late SessionStore store;
  late int notified;

  setUp(() {
    FlutterSecureStorage.setMockInitialValues({});
    api = FakeApi();
    store = SessionStore();
    notified = 0;
  });

  /// The service wired as in main.dart: the client reads its token and
  /// reports 401s back to it; [origin] stands in for the server address.
  TokenAuthService service({
    StoredSession? initial,
    String server = _server,
    ValueNotifier<String>? origin,
    String? personalKey,
  }) {
    late final TokenAuthService auth;
    final client = api.client(
      tokenProvider: () => auth.accessToken,
      onUnauthorized: (token) => auth.onUnauthorized(token),
    );
    return auth = TokenAuthService(
      client,
      store,
      initial,
      origin ?? ValueNotifier(server),
      personalKey: () => personalKey,
    )..addListener(() => notified++);
  }

  Future<AuthFailure> failure(Future<void> call) async {
    try {
      await call;
    } on AuthFailure catch (e) {
      return e;
    }
    fail('expected AuthFailure');
  }

  group('sign-in and sign-up', () {
    test('signing in stores the session and notifies', () async {
      Map<String, dynamic>? sent;
      api.on('POST /api/v1/auth/login', (_, body) {
        sent = body;
        return _authJson('rdo_new');
      });
      final auth = service();

      await auth.signIn(email: 'me@example.com', password: 'a-good-password');

      expect(sent, {'email': 'me@example.com', 'password': 'a-good-password'});
      expect(auth.accessToken, 'rdo_new');
      expect(auth.userId, 'user-1');
      expect(auth.email, 'me@example.com');
      expect(notified, 1);
      final stored = (await store.read())!;
      expect(stored.token, 'rdo_new');
      expect(stored.server, _server);
      expect(stored.expiresAt, DateTime.utc(2026, 11, 22, 10));
    });

    test(
      'signing up stores the session; the invite code only when given',
      () async {
        final bodies = <Map<String, dynamic>>[];
        api.on('POST /api/v1/auth/register', (_, body) {
          bodies.add(body);
          return _authJson('rdo_${bodies.length}');
        });
        final auth = service();

        await auth.signUp(email: 'a@example.com', password: 'a-good-password');
        await auth.signUp(
          email: 'b@example.com',
          password: 'a-good-password',
          inviteCode: '  ',
        );
        await auth.signUp(
          email: 'c@example.com',
          password: 'a-good-password',
          inviteCode: ' let-me-in ',
        );

        expect(bodies[0].containsKey('invite_code'), isFalse);
        expect(bodies[1].containsKey('invite_code'), isFalse);
        expect(bodies[2]['invite_code'], 'let-me-in');
        expect(auth.accessToken, 'rdo_3');
        expect((await store.read())!.token, 'rdo_3');
        expect(notified, 3);
      },
    );

    test('a personal server signs its one user in without a body', () async {
      http.Request? sent;
      api.on('POST /api/v1/auth/personal', (request, _) {
        sent = request;
        return _authJson('rdo_personal', email: 'personal@localhost');
      });
      final auth = service();

      await auth.signInPersonal();

      expect(sent!.body, isEmpty);
      expect(sent!.headers['Authorization'], isNull);
      expect(auth.accessToken, 'rdo_personal');
      expect(auth.email, 'personal@localhost');
      expect((await store.read())!.server, _server);
    });

    test('the personal key goes along as X-Personal-Key', () async {
      String? sentKey;
      api.on('POST /api/v1/auth/personal', (request, _) {
        sentKey = request.headers['X-Personal-Key'];
        return _authJson('rdo_personal', email: 'personal@localhost');
      });

      await service(personalKey: 'k3y_from-the-link').signInPersonal();

      expect(sentKey, 'k3y_from-the-link');
    });

    test('a missing or wrong key is personalKey', () async {
      for (final detail in ['personal key required', 'wrong personal key']) {
        String? sentKey = 'unset';
        api.on('POST /api/v1/auth/personal', (request, _) {
          sentKey = request.headers['X-Personal-Key'];
          throw FakeApiError(403, detail);
        });
        final key = detail == 'wrong personal key' ? 'an-old-key-123456' : null;

        final e = await failure(service(personalKey: key).signInPersonal());

        expect(e.kind, AuthFailureKind.personalKey, reason: detail);
        expect(sentKey, key, reason: detail);
      }
    });

    test('a personal sign-in from another machine fails', () async {
      api.on(
        'POST /api/v1/auth/personal',
        (_, _) =>
            throw FakeApiError(403, 'personal sign-in only on this machine'),
      );
      final auth = service();

      final e = await failure(auth.signInPersonal());

      expect(e.kind, AuthFailureKind.other);
      expect(auth.isSignedIn, isFalse);
    });

    test('each refusal becomes its AuthFailure kind', () async {
      final cases = <(String, FakeApiError, AuthFailureKind)>[
        (
          'login',
          FakeApiError(401, 'invalid email or password'),
          AuthFailureKind.invalidCredentials,
        ),
        (
          'register',
          FakeApiError(409, 'email already registered'),
          AuthFailureKind.emailTaken,
        ),
        (
          'register',
          FakeApiError(403, 'registration closed'),
          AuthFailureKind.registrationClosed,
        ),
        (
          'register',
          FakeApiError(403, 'invalid invite code'),
          AuthFailureKind.inviteInvalid,
        ),
        (
          'register',
          FakeApiError(422, 'String should have at least 8 characters'),
          AuthFailureKind.invalidInput,
        ),
        (
          'register',
          FakeApiError(403, 'something new'),
          AuthFailureKind.other,
        ),
        ('login', FakeApiError(500, 'boom'), AuthFailureKind.other),
      ];
      for (final (route, error, kind) in cases) {
        api.on('POST /api/v1/auth/$route', (_, _) => throw error);
        final auth = service();
        final call = route == 'login'
            ? auth.signIn(email: 'me@example.com', password: 'whatever-1')
            : auth.signUp(email: 'me@example.com', password: 'whatever-1');
        final e = await failure(call);
        expect(e.kind, kind, reason: '${error.status} ${error.message}');
        expect(e.retryAfterSeconds, isNull);
        expect(auth.isSignedIn, isFalse);
      }
    });

    test('too many attempts carries Retry-After', () async {
      api
        ..on(
          'POST /api/v1/auth/login',
          (_, _) => throw FakeApiError(
            429,
            'too many attempts, try again later',
            headers: {'retry-after': '600'},
          ),
        )
        ..on(
          'POST /api/v1/auth/register',
          (_, _) =>
              throw FakeApiError(429, 'too many attempts, try again later'),
        );
      final auth = service();

      final login = await failure(
        auth.signIn(email: 'me@example.com', password: 'whatever-1'),
      );
      expect(login.kind, AuthFailureKind.tooManyAttempts);
      expect(login.retryAfterSeconds, 600);
      final register = await failure(
        auth.signUp(email: 'me@example.com', password: 'whatever-1'),
      );
      expect(register.kind, AuthFailureKind.tooManyAttempts);
      expect(register.retryAfterSeconds, isNull);
    });

    test('a session the keychain does not keep fails visibly', () async {
      FlutterSecureStorage.setMockInitialValues(DroppedWrites());
      api.on('POST /api/v1/auth/login', (_, _) => _authJson('rdo_new'));
      final auth = service();

      // An Exception, so the screens show their generic error.
      await expectLater(
        auth.signIn(email: 'me@example.com', password: 'a-good-password'),
        throwsA(isA<SessionWriteException>()),
      );
      expect(auth.isSignedIn, isFalse);
      expect(notified, 0);
    });

    test('offline stays a NetworkException', () async {
      api.offline = true;
      await expectLater(
        service().signIn(email: 'me@example.com', password: 'whatever-1'),
        throwsA(isA<NetworkException>()),
      );
    });
  });

  group('signing out', () {
    test('revokes the token, clears the store and notifies', () async {
      await store.write(_stored());
      String? revoked;
      api.on('POST /api/v1/auth/logout', (request, _) {
        revoked = request.headers['Authorization'];
        return null;
      });
      final auth = service(initial: _stored());

      await auth.signOut();

      expect(revoked, 'Bearer rdo_old');
      expect(auth.isSignedIn, isFalse);
      expect(await store.read(), isNull);
      expect(notified, greaterThan(0));
    });

    test('clears even when logout fails, answers 401 or is offline', () async {
      final failures = <void Function()>[
        () => api.on(
          'POST /api/v1/auth/logout',
          (_, _) => throw FakeApiError(500, 'boom'),
        ),
        // After account deletion the token is already gone.
        () => api.on(
          'POST /api/v1/auth/logout',
          (_, _) => throw FakeApiError(401, 'not authenticated'),
        ),
        () => api.offline = true,
      ];
      for (final setUpFailure in failures) {
        api = FakeApi();
        setUpFailure();
        await store.write(_stored());
        notified = 0;
        final auth = service(initial: _stored());

        await auth.signOut();

        expect(auth.isSignedIn, isFalse);
        expect(await store.read(), isNull);
        expect(notified, greaterThan(0));
      }
    });
  });

  group('account changes', () {
    test('changing the password keeps the session', () async {
      await store.write(_stored());
      Map<String, dynamic>? sent;
      api.on('POST /api/v1/auth/password', (_, body) {
        sent = body;
        return null;
      });
      final auth = service(initial: _stored());

      await auth.updatePassword(
        currentPassword: 'old-password',
        newPassword: 'new-password',
      );

      expect(sent, {
        'current_password': 'old-password',
        'new_password': 'new-password',
      });
      expect(auth.accessToken, 'rdo_old');
      expect((await store.read())!.token, 'rdo_old');
    });

    test(
      'a wrong current password is wrongPassword, still signed in',
      () async {
        api
          ..on(
            'POST /api/v1/auth/password',
            (_, _) => throw FakeApiError(403, 'wrong password'),
          )
          ..on(
            'POST /api/v1/auth/email',
            (_, _) => throw FakeApiError(403, 'wrong password'),
          );
        final auth = service(initial: _stored());

        final password = await failure(
          auth.updatePassword(
            currentPassword: 'nope',
            newPassword: 'new-password',
          ),
        );
        final email = await failure(
          auth.updateEmail(
            currentPassword: 'nope',
            newEmail: 'new@example.com',
          ),
        );
        expect(password.kind, AuthFailureKind.wrongPassword);
        expect(email.kind, AuthFailureKind.wrongPassword);
        expect(auth.isSignedIn, isTrue);
      },
    );

    test('changing the email stores the new address and notifies', () async {
      await store.write(_stored());
      Map<String, dynamic>? sent;
      api.on('POST /api/v1/auth/email', (_, body) {
        sent = body;
        return {'id': 'user-1', 'email': 'new@example.com'};
      });
      final auth = service(initial: _stored());

      await auth.updateEmail(
        currentPassword: 'old-password',
        newEmail: 'New@Example.com',
      );

      expect(sent, {
        'current_password': 'old-password',
        'new_email': 'New@Example.com',
      });
      expect(auth.email, 'new@example.com');
      expect(notified, 1);
      final stored = (await store.read())!;
      expect(stored.email, 'new@example.com');
      expect(stored.token, 'rdo_old');
    });

    test('an email in use is emailTaken', () async {
      api.on(
        'POST /api/v1/auth/email',
        (_, _) => throw FakeApiError(409, 'email already registered'),
      );
      final e = await failure(
        service(initial: _stored()).updateEmail(
          currentPassword: 'old-password',
          newEmail: 'taken@example.com',
        ),
      );
      expect(e.kind, AuthFailureKind.emailTaken);
    });
  });

  group('startup and 401s', () {
    test('validate keeps a live session', () async {
      api.on('POST /api/v1/auth/session/validate', (_, _) => null);
      final auth = service(initial: _stored());

      await auth.validate();

      expect(auth.isSignedIn, isTrue);
      expect(notified, 0);
    });

    test('validate clears a session the server no longer knows', () async {
      await store.write(_stored());
      api.on(
        'POST /api/v1/auth/session/validate',
        (_, _) => throw FakeApiError(401, 'not authenticated'),
      );
      final auth = service(initial: _stored());

      await auth.validate();

      expect(auth.isSignedIn, isFalse);
      expect(await store.read(), isNull);
      expect(notified, greaterThan(0));
    });

    test('validate keeps the session offline or on a server error', () async {
      await store.write(_stored());
      api.on(
        'POST /api/v1/auth/session/validate',
        (_, _) => throw FakeApiError(503, 'unavailable'),
      );
      final auth = service(initial: _stored());

      await auth.validate();
      api.offline = true;
      await auth.validate();

      expect(auth.accessToken, 'rdo_old');
      expect((await store.read())!.token, 'rdo_old');
      expect(notified, 0);
    });

    test('a 401 on any route signs out', () async {
      await store.write(_stored());
      api.on(
        'GET /api/v1/dreams',
        (_, _) => throw FakeApiError(401, 'not authenticated'),
      );
      late final TokenAuthService auth;
      final client = api.client(
        tokenProvider: () => auth.accessToken,
        onUnauthorized: (token) => auth.onUnauthorized(token),
      );
      auth = TokenAuthService(client, store, _stored(), ValueNotifier(_server));

      await expectLater(
        client.getList('/api/v1/dreams'),
        throwsA(isA<ApiException>()),
      );

      expect(auth.isSignedIn, isFalse);
      expect(await store.read(), isNull);
    });

    test('a stale 401 is retried only for the same user', () async {
      var issued = 0;
      api.on(
        'POST /api/v1/auth/login',
        (_, _) => _authJson('rdo_${++issued}'),
      );
      final auth = service();
      await auth.signIn(email: 'me@example.com', password: 'whatever-1');
      await auth.signIn(email: 'me@example.com', password: 'whatever-1');
      notified = 0;

      expect(await auth.onUnauthorized('rdo_1'), isTrue);
      // Sent before anyone signed in, or with a token this app never held.
      expect(await auth.onUnauthorized(null), isFalse);
      expect(await auth.onUnauthorized('rdo_unknown'), isFalse);
      expect(auth.accessToken, 'rdo_2');
      expect(notified, 0);

      expect(await auth.onUnauthorized('rdo_2'), isFalse);
      expect(auth.isSignedIn, isFalse);
      expect(notified, 1);
    });

    test(
      'a request refused across a switch of account stays refused',
      () async {
        // A small server that checks the token only when a held request has
        // fully arrived, as FastAPI does after reading the body.
        final live = <String, String>{'rdo_a': 'user-a'};
        final saved = <String, int>{};
        final sent = <String?>[];
        var issued = 0;
        late Completer<void> wire;
        Future<http.Response> handle(http.Request request) async {
          final token = request.headers['Authorization']?.substring(7);
          switch (request.url.path) {
            case '/api/v1/auth/login':
              final email =
                  (jsonDecode(request.body) as Map)['email'] as String;
              final user = 'user-${email[0]}';
              final fresh = 'rdo_${email[0]}${++issued}';
              live[fresh] = user;
              return http.Response(
                jsonEncode({
                  'token': fresh,
                  'user': {'id': user, 'email': email},
                }),
                200,
              );
            case '/api/v1/auth/logout':
              live.remove(token);
              return http.Response('', 204);
          }
          sent.add(token);
          await wire.future;
          final user = live[token];
          if (user == null) {
            return http.Response('{"detail": "not authenticated"}', 401);
          }
          saved[user] = (saved[user] ?? 0) + 1;
          return http.Response('{}', 201);
        }

        late final TokenAuthService auth;
        final client = ApiClient(
          baseUrl: () => _server,
          tokenProvider: () => auth.accessToken,
          onUnauthorized: (token) => auth.onUnauthorized(token),
          httpClient: MockClient(handle),
        );
        auth = TokenAuthService(
          client,
          store,
          const StoredSession(
            token: 'rdo_a',
            userId: 'user-a',
            email: 'a@example.com',
            server: _server,
          ),
          ValueNotifier(_server),
        );

        /// Saves a dream while its sender signs out and [email] signs in.
        Future<Object?> saveAcrossSignIn(String email) async {
          sent.clear();
          wire = Completer<void>();
          final save = client.post('/api/v1/dreams', {'text': 'a dream'});
          await pumpEventQueue();
          await auth.signOut();
          await auth.signIn(email: email, password: 'whatever-1');
          wire.complete();
          try {
            return await save;
          } on ApiException catch (e) {
            return e;
          }
        }

        // Someone else signed in: their account must not get A's dream.
        expect(
          await saveAcrossSignIn('b@example.com'),
          isA<ApiException>().having((e) => e.statusCode, 'status', 401),
        );
        expect(sent, ['rdo_a']);
        expect(saved, isEmpty);
        expect(auth.userId, 'user-b');
        expect((await store.read())!.userId, 'user-b');

        // The same person signed in again: the request goes through once more.
        final before = auth.accessToken;
        expect(
          await saveAcrossSignIn('b@example.com'),
          isA<Map<String, dynamic>>(),
        );
        expect(sent, [before, auth.accessToken]);
        expect(saved, {'user-b': 1});
      },
    );

    test('changing the server ends the session it belongs to', () async {
      await store.write(_stored());
      final origin = ValueNotifier(_server);
      final auth = service(initial: _stored(), origin: origin);
      expect(auth.isSignedIn, isTrue);

      origin.value = '';
      await pumpEventQueue();

      expect(auth.isSignedIn, isFalse);
      expect(auth.accessToken, isNull);
      expect(await store.read(), isNull);
    });

    test("the server before's tokens are never retried on the next", () async {
      await store.write(_stored());
      final origin = ValueNotifier(_server);
      api.on('POST /api/v1/auth/login', (_, _) => _authJson('rdo_new'));
      final auth = service(initial: _stored(), origin: origin);

      // Same user id on another server (a moved or cloned instance).
      origin.value = 'http://fake/other';
      await auth.signIn(email: 'me@example.com', password: 'pw');

      expect(auth.accessToken, 'rdo_new');
      expect(await auth.onUnauthorized('rdo_old'), isFalse);
      expect(auth.accessToken, 'rdo_new');
    });

    test('signing out does not wait for a server that is down', () async {
      await store.write(_stored());
      final answer = Completer<void>();
      api.on('POST /api/v1/auth/logout', (_, _) => null);
      final auth = service(initial: _stored());
      api.delay = answer.future;

      final signOut = auth.signOut();

      expect(auth.isSignedIn, isFalse);
      answer.complete();
      await signOut;
      expect(api.calls, ['POST /api/v1/auth/logout']);
      expect(await store.read(), isNull);
    });

    test('a sign-in answered after a server change is dropped', () async {
      final answer = Completer<void>();
      final origin = ValueNotifier(_server);
      api.on('POST /api/v1/auth/login', (_, _) => _authJson('rdo_new'));
      final auth = service(origin: origin);
      api.delay = answer.future;

      final signIn = auth.signIn(email: 'me@example.com', password: 'pw');
      await pumpEventQueue();
      origin.value = 'https://other.example';
      answer.complete();
      await signIn;

      expect(auth.isSignedIn, isFalse);
      expect(await store.read(), isNull);
    });

    test('a session for another server is dropped, never sent', () async {
      await store.write(_stored(server: 'https://other.example'));
      api.on('POST /api/v1/auth/session/validate', (_, _) => null);
      final auth = service(initial: _stored(server: 'https://other.example'));

      expect(auth.isSignedIn, isFalse);
      expect(auth.accessToken, isNull);
      await auth.validate();
      expect(api.calls, isEmpty);
      await pumpEventQueue();
      expect(await store.read(), isNull);
    });
  });
}
