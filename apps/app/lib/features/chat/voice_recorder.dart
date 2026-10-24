import 'package:flutter/foundation.dart';
import 'package:record/record.dart';
import 'package:redream_app/features/chat/recording_file.dart'
    if (dart.library.js_interop) 'package:redream_app/features/chat/recording_file_web.dart';

/// A finished voice recording, ready for transcription.
class VoiceRecording {
  const VoiceRecording({required this.bytes, required this.fileName});
  final Uint8List bytes;
  final String fileName;
}

/// Thin seam over the `record` plugin so `ChatState` and widget tests never
/// touch platform channels directly.
abstract class IVoiceRecorder {
  Future<bool> hasPermission();
  Future<void> start();

  /// Stops recording and returns the audio, or null when nothing was
  /// captured.
  Future<VoiceRecording?> stop();
  Future<void> cancel();
  Future<void> dispose();
}

/// Production recorder. Records WAV (16 kHz mono) because the STT providers
/// accept WAV/MP3 only — the plugin's AAC default would be rejected. A few
/// minutes of dream narration stays well under the backend's 25 MB cap.
class RecordVoiceRecorder implements IVoiceRecorder {
  final AudioRecorder _recorder = AudioRecorder();

  static const _fileName = 'dream_recording.wav';

  @override
  Future<bool> hasPermission() => _recorder.hasPermission();

  @override
  Future<void> start() async {
    await _recorder.start(
      const RecordConfig(
        encoder: AudioEncoder.wav,
        sampleRate: 16000,
        numChannels: 1,
      ),
      path: await recordingTarget(_fileName),
    );
  }

  @override
  Future<VoiceRecording?> stop() async {
    final path = await _recorder.stop();
    if (path == null) return null;
    final bytes = await takeRecording(path);
    if (bytes == null || bytes.isEmpty) return null;
    return VoiceRecording(bytes: bytes, fileName: _fileName);
  }

  @override
  Future<void> cancel() async {
    await _recorder.cancel();
  }

  @override
  Future<void> dispose() => _recorder.dispose();
}
