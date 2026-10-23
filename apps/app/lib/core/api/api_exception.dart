/// Non-2xx response from the backend.
class ApiException implements Exception {
  ApiException(this.statusCode, this.message, {this.retryAfterSeconds});

  final int statusCode;
  final String message;
  final int? retryAfterSeconds;

  bool get isUnauthorized => statusCode == 401;
  bool get isNotFound => statusCode == 404;
  bool get isConflict => statusCode == 409;
  bool get isRateLimited => statusCode == 429;

  /// The server's per-user daily cap (`DAILY_AI_REQUESTS_PER_USER`); it
  /// resets at midnight UTC. Other 429s are short throttles.
  bool get isDailyLimit =>
      statusCode == 429 && message == 'daily limit reached';

  /// Speech input or output is not configured on this server.
  bool get isVoiceUnavailable =>
      statusCode == 503 && message == 'voice_unavailable';

  /// Health-data routes refuse until Art. 9 consent is granted.
  bool get isConsentRequired =>
      statusCode == 403 && message == 'consent_required';

  @override
  String toString() => 'ApiException($statusCode): $message';
}

/// The request never got a response: no connectivity, DNS failure, timeout.
class NetworkException implements Exception {
  NetworkException(this.message);

  final String message;

  @override
  String toString() => 'NetworkException: $message';
}
