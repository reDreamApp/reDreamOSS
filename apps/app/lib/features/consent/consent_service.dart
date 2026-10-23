import 'package:redream_app/core/api/api_client.dart';

/// Version of the consent copy bundled in this app, kept in step with ARBs.
const consentTextVersion = '2026-10-23';

/// Backend consent status (`GET /users/me/consent`).
class ConsentStatus {
  const ConsentStatus({
    required this.requiredVersion,
    required this.grantedVersion,
    required this.consentRequired,
  });

  factory ConsentStatus.fromJson(Map<String, dynamic> json) => ConsentStatus(
    requiredVersion: json['required_version'] as String,
    grantedVersion: json['granted_version'] as String?,
    consentRequired: json['consent_required'] as bool,
  );

  final String requiredVersion;
  final String? grantedVersion;
  final bool consentRequired;
}

/// Art. 9 explicit-consent API (one bundled consent for the whole
/// processing chain — dream content, AI providers, diagnostics).
abstract class IConsentService {
  Future<ConsentStatus> status();

  /// Records the grant for [version] — the consent-text version the user
  /// actually saw (the backend rejects stale versions with 409).
  Future<void> grant(String version);

  /// Withdrawal — as easy as granting (Art. 7(3)). Health-data features
  /// gate until re-consent; the user's data stays until they delete it.
  Future<void> withdraw();
}

class ApiConsentService implements IConsentService {
  ApiConsentService(this._api);
  final ApiClient _api;

  static const _path = '/api/v1/users/me/consent';

  @override
  Future<ConsentStatus> status() async =>
      ConsentStatus.fromJson(await _api.get(_path));

  @override
  Future<void> grant(String version) => _api.post(_path, {'version': version, 'text_version': consentTextVersion});

  @override
  Future<void> withdraw() => _api.delete(_path);
}
