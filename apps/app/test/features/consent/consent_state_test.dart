import 'package:flutter_test/flutter_test.dart';
import 'package:redream_app/core/api/api_exception.dart';
import 'package:redream_app/features/consent/consent_service.dart';
import 'package:redream_app/features/consent/consent_state.dart';

class _FakeConsentService implements IConsentService {
  bool granted = false;
  String requiredVersion = '2026-10-23';
  bool failStatus = false;
  bool failGrant = false;
  ApiException grantError = ApiException(500, 'grant failed');
  int statusCalls = 0;
  final List<String> grantCalls = [];
  int withdrawCalls = 0;

  @override
  Future<ConsentStatus> status() async {
    statusCalls++;
    if (failStatus) throw ApiException(500, 'status failed');
    return ConsentStatus(
      requiredVersion: requiredVersion,
      grantedVersion: granted ? requiredVersion : null,
      consentRequired: !granted,
    );
  }

  @override
  Future<void> grant(String version) async {
    if (failGrant) throw grantError;
    grantCalls.add(version);
    granted = true;
  }

  @override
  Future<void> withdraw() async {
    withdrawCalls++;
    granted = false;
  }
}

void main() {
  group('ConsentState', () {
    test('starts unknown; load resolves consentRequired', () async {
      final service = _FakeConsentService();
      final state = ConsentState(service);

      expect(state.consentRequired, isNull);
      await state.load();

      expect(state.consentRequired, isTrue);
      expect(state.requiredVersion, '2026-10-23');
    });

    test('load failure surfaces errorMessage, stays unknown', () async {
      final service = _FakeConsentService()..failStatus = true;
      final state = ConsentState(service);

      await state.load();

      expect(state.consentRequired, isNull);
      expect(state.errorMessage, 'status failed');
    });

    test('grant records the version the user saw and unblocks', () async {
      final service = _FakeConsentService();
      final state = ConsentState(service);
      await state.load();

      await state.grant();

      expect(service.grantCalls, ['2026-10-23']);
      expect(state.consentRequired, isFalse);
    });

    test('a newer server version cannot be granted with old bundled text', () async {
      final service = _FakeConsentService()..requiredVersion = '2099-01-01';
      final state = ConsentState(service);
      await state.load();
      await state.grant();
      expect(state.hasCurrentText, isFalse);
      expect(state.consentRequired, isTrue);
      expect(service.grantCalls, isEmpty);
    });

    test('grant before load is a no-op (no version on screen yet)', () async {
      final service = _FakeConsentService();
      final state = ConsentState(service);

      await state.grant();

      expect(service.grantCalls, isEmpty);
    });

    test('409 on grant (stale consent text) refetches the status', () async {
      final service = _FakeConsentService()
        ..failGrant = true
        ..grantError = ApiException(409, 'consent text changed');
      final state = ConsentState(service);
      await state.load();
      service.requiredVersion = '2027-01-01';

      await state.grant();

      // Still blocked, but the screen now has the CURRENT version.
      expect(state.consentRequired, isTrue);
      expect(state.requiredVersion, '2027-01-01');
      expect(state.errorMessage, isNull);
    });

    test('grant failure keeps the screen blocked with an error', () async {
      final service = _FakeConsentService()..failGrant = true;
      final state = ConsentState(service);
      await state.load();

      await state.grant();

      expect(state.consentRequired, isTrue);
      expect(state.errorMessage, 'grant failed');
    });

    test('withdraw re-blocks and reports success', () async {
      final service = _FakeConsentService()..granted = true;
      final state = ConsentState(service);
      await state.load();
      expect(state.consentRequired, isFalse);

      final ok = await state.withdraw();

      expect(ok, isTrue);
      expect(service.withdrawCalls, 1);
      expect(state.consentRequired, isTrue);
    });
  });
}
