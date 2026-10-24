import 'package:flutter/foundation.dart';

/// What this platform offers the app. The web app has no file system of its
/// own (no offline rehearsal copies, no files left to clean up) and no local
/// notifications (no daily reminder).
abstract final class PlatformFeatures {
  /// Lets widget tests show the web app's screens.
  @visibleForTesting
  static bool? debugIsWeb;

  static bool get isWeb => debugIsWeb ?? kIsWeb;
  static bool get reminders => !isWeb;
  static bool get offlineAudio => !isWeb;
}
