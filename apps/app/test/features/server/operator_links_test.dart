import 'package:flutter_test/flutter_test.dart';
import 'package:redream_app/features/server/operator_links.dart';

void main() {
  test('an email becomes mailto, a web address stays, other text has none', () {
    expect(
      contactUri(' help@dreams.example.org ').toString(),
      'mailto:help@dreams.example.org',
    );
    expect(
      contactUri('https://dreams.example.org/help').toString(),
      'https://dreams.example.org/help',
    );
    expect(contactUri('Call Anna on Mondays'), isNull);
    expect(contactUri('javascript:alert(1)'), isNull);
    expect(contactUri('mailto:x@y.org?body=hi'), isNull);
    expect(contactUri('a@example.org,b@evil.example'), isNull);
    expect(contactUri('a@example.org; b@evil.example'), isNull);
  });

  test('a privacy link must be a web address with a host', () {
    expect(webUri('https://dreams.example.org/privacy'), isNotNull);
    expect(webUri('file:///etc/passwd'), isNull);
    expect(webUri('https://'), isNull);
    expect(webUri('https://user:pw@dreams.example.org'), isNull);
    expect(webUri(''), isNull);
  });
}
