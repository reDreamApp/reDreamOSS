enum Mood {
  veryDissatisfied('Very Dissatisfied', 1),
  dissatisfied('Dissatisfied', 2),
  neutral('Neutral', 3),
  satisfied('Satisfied', 4),
  verySatisfied('Very Satisfied', 5)
  ;

  const Mood(this.wire, this.score);

  final String wire;
  final int score;

  static Mood? fromWire(String? value) =>
      values.where((m) => m.wire == value).firstOrNull;

  static Mood? fromScore(double? score) => score == null
      ? null
      : values.reduce(
          (a, b) => (a.score - score).abs() <= (b.score - score).abs() ? a : b,
        );
}

class ProgressStats {
  const ProgressStats({
    required this.rehearsalStreak,
    required this.dreamsLogged,
    required this.dreamsRewritten,
    this.averageIntensity,
    this.averageMood,
    this.todayMood,
  });

  factory ProgressStats.fromJson(Map<String, dynamic> json) => ProgressStats(
    rehearsalStreak: json['rehearsal_streak'] as int? ?? 0,
    averageIntensity: (json['average_intensity'] as num?)?.toDouble(),
    averageMood: (json['average_mood'] as num?)?.toDouble(),
    todayMood: Mood.fromWire(json['today_mood'] as String?),
    dreamsLogged: json['dreams_logged'] as int? ?? 0,
    dreamsRewritten: json['dreams_rewritten'] as int? ?? 0,
  );

  final int rehearsalStreak;
  final double? averageIntensity;
  final double? averageMood;
  final Mood? todayMood;
  final int dreamsLogged;
  final int dreamsRewritten;
}
