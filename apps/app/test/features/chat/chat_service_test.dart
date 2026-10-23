import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:redream_app/core/api/api_client.dart';
import 'package:redream_app/core/api/api_exception.dart';
import 'package:redream_app/features/chat/chat_models.dart';
import 'package:redream_app/features/chat/chat_service.dart';

const _userMessageJson =
    '{"id": "u1", "role": "user", "content": "hi", '
    '"stage": null, "visibility_status": "visible", '
    '"created_at": "2026-07-01T10:00:00Z"}';

const _aiMessageJson =
    '{"id": "a1", "role": "assistant", "content": "Hello", '
    '"stage": "recording", "visibility_status": "visible", '
    '"created_at": "2026-07-01T10:00:01Z"}';

const _doneFrame =
    'data: {"done": {"chat_id": 1, "ai_message": $_aiMessageJson, '
    '"stage": "rewriting", "is_done": false}}\n\n';

void main() {
  group('parseSseStream', () {
    Future<List<ChatStreamEvent>> parse(List<String> chunks) =>
        parseSseStream(Stream.fromIterable(chunks)).toList();

    test('parses a full normal turn', () async {
      final events = await parse([
        'data: {"user_message": $_userMessageJson}\n\n',
        'data: {"content": "Hel", "stages": ["recording"], "language": "en"}\n\n',
        'data: {"content": "lo", "stages": ["recording"], "language": "en"}\n\n',
        _doneFrame,
        'data: [DONE]\n\n',
      ]);
      expect(events, hasLength(4));
      final user = events[0] as UserMessageEvent;
      expect(user.message.id, 'u1');
      expect(user.message.isUser, isTrue);
      expect((events[1] as ContentDeltaEvent).delta, 'Hel');
      expect((events[2] as ContentDeltaEvent).delta, 'lo');
      final done = events[3] as DoneEvent;
      expect(done.aiMessage.id, 'a1');
      expect(done.aiMessage.content, 'Hello');
      expect(done.stage, IrtStage.rewriting);
      expect(done.isDone, isFalse);
    });

    test('reassembles frames split across chunk boundaries', () async {
      const frame = 'data: {"user_message": $_userMessageJson}\n\n';
      final events = await parse([
        frame.substring(0, 25),
        frame.substring(25, 60),
        frame.substring(60),
        'data: {"content": "He',
        'llo"}\n\ndata: [DO',
        'NE]\n\n',
      ]);
      expect(events, hasLength(2));
      expect((events[0] as UserMessageEvent).message.content, 'hi');
      expect((events[1] as ContentDeltaEvent).delta, 'Hello');
    });

    test('[DONE] ends the stream even when more data follows', () async {
      final events = await parse([
        'data: {"content": "a"}\n\ndata: [DONE]\n\ndata: {"content": "b"}\n\n',
      ]);
      expect(events, hasLength(1));
      expect((events.single as ContentDeltaEvent).delta, 'a');
    });

    test('maps the safety path to SafetyEventEvent', () async {
      const safetyFrame =
          'data: {"safety_event": {"risk": "high", "categories": ["self_harm"], '
          '"confidence": 0.9, "message": "helpline"}}\n\n';
      final events = await parse([
        'data: {"user_message": $_userMessageJson}\n\n',
        safetyFrame,
        'data: [DONE]\n\n',
      ]);
      expect(events, hasLength(2));
      final safety = events[1] as SafetyEventEvent;
      expect(safety.safetyEvent.risk, 'high');
      expect(safety.safetyEvent.categories, ['self_harm']);
      expect(safety.safetyEvent.message, 'helpline');
    });

    test('maps an error frame to ErrorEvent', () async {
      final events = await parse([
        'data: {"user_message": $_userMessageJson}\n\n',
        'data: {"error": "upstream failed"}\n\n',
        'data: [DONE]\n\n',
      ]);
      expect(events, hasLength(2));
      expect((events[1] as ErrorEvent).message, 'upstream failed');
    });

    test(
      'ignores unknown frames and non-JSON payloads without crashing',
      () async {
        final events = await parse([
          'data: {"heartbeat": true}\n\n',
          ': sse comment\n\n',
          'data: not-json\n\n',
          'data: {"content": "ok", "brand_new_key": 42}\n\n',
          'data: [DONE]\n\n',
        ]);
        expect(events, hasLength(1));
        expect((events.single as ContentDeltaEvent).delta, 'ok');
      },
    );

    test(
      'ignores malformed known frames instead of throwing TypeError',
      () async {
        final events = await parse([
          // done: ai_message / stage missing or wrong-typed
          'data: {"done": {"stage": "rewriting"}}\n\n',
          'data: {"done": {"ai_message": "not-a-map", "stage": "rewriting"}}\n\n',
          'data: {"done": {"ai_message": $_aiMessageJson, "stage": 42}}\n\n',
          'data: {"done": {"ai_message": $_aiMessageJson}}\n\n',
          // done: malformed inner message fields
          'data: {"done": {"ai_message": {"id": 1, "role": 5, "content": "x", "created_at": "2026-07-01T10:00:00Z"}, "stage": "recording"}}\n\n',
          'data: {"done": {"ai_message": {"id": 1, "role": "assistant", "content": "x", "created_at": "not a date"}, "stage": "recording"}}\n\n',
          // user_message: missing / wrong-typed fields
          'data: {"user_message": {"id": "u1", "role": "user"}}\n\n',
          'data: {"user_message": {"id": "u1", "role": "user", "content": 7, "created_at": "2026-07-01T10:00:00Z"}}\n\n',
          // safety_event: wrong-typed fields
          'data: {"safety_event": {"risk": "high", "categories": "self_harm", "confidence": 0.9}}\n\n',
          'data: {"safety_event": {"risk": 1, "categories": [], "confidence": "x"}}\n\n',
          'data: {"safety_event": {"risk": "high", "categories": [1, 2], "confidence": 0.9}}\n\n',
          // content / error with wrong types
          'data: {"content": 42}\n\n',
          'data: {"error": {"detail": "x"}}\n\n',
          // one well-formed frame proves the stream survived all of the above
          'data: {"content": "ok"}\n\n',
          'data: [DONE]\n\n',
        ]);
        expect(events, hasLength(1));
        expect((events.single as ContentDeltaEvent).delta, 'ok');
      },
    );

    test('done frame with a non-bool is_done defaults to false', () async {
      final events = await parse([
        'data: {"done": {"ai_message": $_aiMessageJson, "stage": "rewriting", "is_done": "yes"}}\n\n',
        'data: [DONE]\n\n',
      ]);
      final done = events.single as DoneEvent;
      expect(done.isDone, isFalse);
      expect(done.stage, IrtStage.rewriting);
    });
  });

  group('ChatService.sendMessageStream', () {
    ChatService serviceWith(MockClient client) => ChatService(
      ApiClient(
        baseUrl: () => 'http://test',
        tokenProvider: () => 'jwt',
        httpClient: client,
      ),
    );

    test(
      'POSTs the stream endpoint with auth and yields parsed events',
      () async {
        late http.BaseRequest captured;
        late String capturedBody;
        final client = MockClient.streaming((request, bodyStream) async {
          captured = request;
          capturedBody = await utf8.decodeStream(bodyStream);
          final frames = [
            'data: {"user_message": $_userMessageJson}\n\n',
            'data: {"content": "Hel"}\n\n',
            'data: {"content": "lo"}\n\n',
            _doneFrame,
            'data: [DONE]\n\n',
          ];
          return http.StreamedResponse(
            Stream.fromIterable(frames.map(utf8.encode)),
            200,
          );
        });
        final events = await serviceWith(
          client,
        ).sendMessageStream(chatId: 7, content: 'hello').toList();

        expect(captured.method, 'POST');
        expect(captured.url.path, '/api/v1/chats/7/message/stream');
        expect(captured.headers['Authorization'], 'Bearer jwt');
        expect(captured.headers['Accept'], 'text/event-stream');
        expect(jsonDecode(capturedBody), {'content': 'hello'});
        expect(events, hasLength(4));
        expect(events.first, isA<UserMessageEvent>());
        expect(events.last, isA<DoneEvent>());
      },
    );

    test(
      'throws the same ApiException as post() on a pre-stream 429',
      () async {
        final client = MockClient.streaming(
          (request, bodyStream) async => http.StreamedResponse(
            Stream.value(utf8.encode('{"detail": "budget exceeded"}')),
            429,
            headers: {'retry-after': '42'},
          ),
        );
        await expectLater(
          serviceWith(
            client,
          ).sendMessageStream(chatId: 7, content: 'x').toList(),
          throwsA(
            isA<ApiException>()
                .having((e) => e.statusCode, 'statusCode', 429)
                .having((e) => e.retryAfterSeconds, 'retryAfterSeconds', 42)
                .having((e) => e.message, 'message', 'budget exceeded'),
          ),
        );
      },
    );

    test(
      'throws ApiException with the detail message on a pre-stream 409',
      () async {
        final client = MockClient.streaming(
          (request, bodyStream) async => http.StreamedResponse(
            Stream.value(utf8.encode('{"detail": "chat is already finished"}')),
            409,
          ),
        );
        await expectLater(
          serviceWith(
            client,
          ).sendMessageStream(chatId: 7, content: 'x').toList(),
          throwsA(
            isA<ApiException>()
                .having((e) => e.statusCode, 'statusCode', 409)
                .having((e) => e.message, 'message', 'chat is already finished')
                .having(
                  (e) => e.retryAfterSeconds,
                  'retryAfterSeconds',
                  isNull,
                ),
          ),
        );
      },
    );
  });
}
