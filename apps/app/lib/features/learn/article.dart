import 'package:flutter/material.dart';
import 'package:redream_app/l10n/generated/app_localizations.dart';

class Article {
  const Article({
    required this.slug,
    required this.title,
    required this.excerpt,
    required this.urls,
  });

  /// The team's study in Somnologie (2026), the Learning tab's article.
  factory Article.ownStudy(AppLocalizations l10n) => Article(
    slug: 'study-somnologie-2026',
    title: l10n.learnStudyTitle,
    excerpt: l10n.learnStudyExcerpt,
    urls: const {
      'light': 'https://link.springer.com/article/10.1007/s11818-026-00550-w',
    },
  );

  /// Pictures by slug; an article without one shows none.
  static const _images = {
    'study-somnologie-2026': 'assets/images/learn_study.webp',
  };

  final String slug;
  final String title;
  final String excerpt;

  /// The picture on the article's page, if it has one.
  String? get image => _images[slug];

  /// `light` / `dark` → article URL.
  final Map<String, String> urls;

  String? urlFor(Brightness brightness) =>
      urls[brightness == Brightness.dark ? 'dark' : 'light'] ??
      urls.values.firstOrNull;
}
