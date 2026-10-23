/// What a server says about itself (`GET /api/v1/instance`), read before
/// anyone signs in and kept for offline starts.
class InstanceInfo {
  const InstanceInfo({
    required this.apiVersion,
    required this.consentVersion,
    this.name,
    this.registration = 'invite',
    this.dailyAiRequestsPerUser = 0,
    this.chat = false,
    this.transcription = false,
    this.rehearsalAudio = false,
    this.operatorName,
    this.operatorContact,
    this.privacyUrl,
  });

  /// Only the two version fields must be there; anything else that is
  /// missing or of the wrong type falls back to "off" / unknown.
  factory InstanceInfo.fromJson(Object? json) {
    if (json is! Map<String, dynamic>) {
      throw const FormatException('Instance info is not an object');
    }
    final (apiVersion, consentVersion) = (
      json['api_version'],
      json['consent_version'],
    );
    if (apiVersion is! int || consentVersion is! String) {
      throw const FormatException('Instance info without versions');
    }
    final features = json['features'];
    final operator = json['operator'];
    bool feature(String key) =>
        features is Map<String, dynamic> && features[key] == true;
    String? text(Object? map, String key) {
      final value = map is Map<String, dynamic> ? map[key] : null;
      return value is String && value.trim().isNotEmpty ? value.trim() : null;
    }

    return InstanceInfo(
      apiVersion: apiVersion,
      consentVersion: consentVersion,
      name: text(json, 'name'),
      registration: switch (json['registration']) {
        final String mode when registrationModes.contains(mode) => mode,
        _ => 'invite',
      },
      dailyAiRequestsPerUser: switch (json['daily_ai_requests_per_user']) {
        final int cap when cap > 0 => cap,
        _ => 0,
      },
      chat: feature('chat'),
      transcription: feature('transcription'),
      rehearsalAudio: feature('rehearsal_audio'),
      operatorName: text(operator, 'name'),
      operatorContact: text(operator, 'contact'),
      privacyUrl: text(operator, 'privacy_url'),
    );
  }

  static const registrationModes = {'open', 'invite', 'closed'};

  final int apiVersion;

  /// The consent-text version the server asks for.
  final String consentVersion;
  final String? name;

  /// `open`, `invite` or `closed`; an unknown value counts as `invite`, so
  /// the invite field shows rather than hides.
  final String registration;

  /// 0 = no daily cap.
  final int dailyAiRequestsPerUser;
  final bool chat;
  final bool transcription;
  final bool rehearsalAudio;
  final String? operatorName;

  /// Free text: an email address or a URL.
  final String? operatorContact;
  final String? privacyUrl;

  bool get inviteOnly => registration == 'invite';
  bool get registrationClosed => registration == 'closed';

  Map<String, Object?> toJson() => {
    'name': name,
    'api_version': apiVersion,
    'consent_version': consentVersion,
    'registration': registration,
    'daily_ai_requests_per_user': dailyAiRequestsPerUser,
    'features': {
      'chat': chat,
      'transcription': transcription,
      'rehearsal_audio': rehearsalAudio,
    },
    'operator': {
      'name': operatorName,
      'contact': operatorContact,
      'privacy_url': privacyUrl,
    },
  };
}
