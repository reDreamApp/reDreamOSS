import 'package:flutter/material.dart';
import 'package:redream_app/core/theme/app_theme.dart';
import 'package:redream_app/core/widgets/app_button.dart';
import 'package:redream_app/core/widgets/app_card.dart';
import 'package:redream_app/core/widgets/feedback.dart';
import 'package:redream_app/features/learn/article.dart';
import 'package:redream_app/features/learn/article_screen.dart';
import 'package:redream_app/l10n/generated/app_localizations.dart';
import 'package:url_launcher/url_launcher.dart';

/// The Learn section's page colour: the usual background with a wash of
/// the brand turquoise.
Color learnBackground(BuildContext context) {
  final theme = Theme.of(context);
  final dark = theme.brightness == Brightness.dark;
  return Color.alphaBlend(
    BrandColors.turquoise.withValues(alpha: dark ? 0.16 : 0.22),
    theme.scaffoldBackgroundColor,
  );
}

/// Opens the article in the browser; [openUrl] replaces the browser in tests.
Future<void> openArticle(
  BuildContext context,
  Article article, {
  Future<bool> Function(Uri url)? openUrl,
}) async {
  final l10n = AppLocalizations.of(context)!;
  final url = article.urlFor(Theme.of(context).brightness);
  final uri = url == null ? null : Uri.tryParse(url);
  var ok = false;
  if (uri != null) {
    try {
      ok = await (openUrl ?? _inBrowser)(uri);
    } on Exception catch (_) {}
  }
  if (!ok && context.mounted) {
    showAppSnackBar(context, l10n.learnOpenFailed, error: true);
  }
}

Future<bool> _inBrowser(Uri url) =>
    launchUrl(url, mode: LaunchMode.externalApplication);

/// The articles, built into the app. A card opens the article's page; the
/// button under it opens the article itself in the browser.
class LearnScreen extends StatelessWidget {
  const LearnScreen({super.key, this.onBack, this.openUrl});

  /// Puts a back arrow into the bar. As a tab, Learn returns to Home with
  /// it.
  final VoidCallback? onBack;

  /// Replaces the browser in tests.
  final Future<bool> Function(Uri url)? openUrl;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final theme = Theme.of(context);
    final background = learnBackground(context);
    final articles = [Article.ownStudy(l10n)];
    return Scaffold(
      backgroundColor: background,
      appBar: AppBar(
        backgroundColor: background,
        leading: onBack == null ? null : BackButton(onPressed: onBack),
        title: Text(l10n.learnTitle),
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          for (final article in articles)
            AppCard(
              margin: const EdgeInsets.only(bottom: 12),
              onTap: () => Navigator.push(
                context,
                MaterialPageRoute<void>(
                  builder: (_) =>
                      ArticleScreen(article: article, openUrl: openUrl),
                ),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Row(
                    children: [
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              article.title,
                              style: theme.textTheme.titleMedium?.copyWith(
                                fontWeight: FontWeight.w700,
                              ),
                            ),
                            if (article.excerpt.isNotEmpty) ...[
                              const SizedBox(height: 6),
                              Text(
                                article.excerpt,
                                style: theme.textTheme.bodyMedium,
                              ),
                            ],
                          ],
                        ),
                      ),
                      const SizedBox(width: 8),
                      const Icon(Icons.chevron_right),
                    ],
                  ),
                  const SizedBox(height: 14),
                  AppButton(
                    label: l10n.learnOpenArticle,
                    icon: Icons.open_in_new,
                    style: AppButtonStyle.gradient,
                    onPressed: () =>
                        openArticle(context, article, openUrl: openUrl),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }
}
