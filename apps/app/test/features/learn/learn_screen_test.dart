import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:redream_app/features/learn/article.dart';
import 'package:redream_app/features/learn/article_screen.dart';
import 'package:redream_app/features/learn/learn_screen.dart';
import 'package:redream_app/l10n/generated/app_localizations.dart';
import 'package:redream_app/l10n/generated/app_localizations_de.dart';
import 'package:redream_app/l10n/generated/app_localizations_en.dart';

const _studyUrl =
    'https://link.springer.com/article/10.1007/s11818-026-00550-w';

/// Learn as built into the app; [opened] collects the links it opens.
Future<void> _pumpLearn(WidgetTester tester, {List<Uri>? opened}) async {
  await tester.pumpWidget(
    MaterialApp(
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      home: LearnScreen(
        openUrl: (url) async {
          opened?.add(url);
          return true;
        },
      ),
    ),
  );
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('lists the team study without fetching anything', (
    tester,
  ) async {
    await _pumpLearn(tester);

    expect(
      find.text('Background: rewriting nightmares with a web application'),
      findsOneWidget,
    );
    expect(find.byType(CircularProgressIndicator), findsNothing);
    expect(find.byType(RefreshIndicator), findsNothing);
  });

  testWidgets('the button under an article opens it in the browser', (
    tester,
  ) async {
    final opened = <Uri>[];
    await _pumpLearn(tester, opened: opened);

    // One turquoise button per article, right on the list.
    final buttons = find.widgetWithText(FilledButton, 'Open the article');
    expect(buttons, findsOneWidget);
    await tester.tap(buttons);
    await tester.pumpAndSettle();

    expect(opened.map((u) => u.toString()), [_studyUrl]);
    // Still on the list: no page in between.
    expect(find.byType(ArticleScreen), findsNothing);
  });

  testWidgets('a card opens the article page with its picture and link', (
    tester,
  ) async {
    final opened = <Uri>[];
    await _pumpLearn(tester, opened: opened);

    await tester.tap(
      find.text('Background: rewriting nightmares with a web application'),
    );
    await tester.pumpAndSettle();

    expect(find.byType(ArticleScreen), findsOneWidget);
    expect(
      find.image(const AssetImage('assets/images/learn_study.webp')),
      findsOneWidget,
    );
    expect(opened, isEmpty);
    await tester.tap(find.widgetWithText(FilledButton, 'Open the article'));
    await tester.pumpAndSettle();
    expect(opened.single.toString(), _studyUrl);
  });

  test('only the study has a picture', () {
    expect(
      Article.ownStudy(AppLocalizationsEn()).image,
      'assets/images/learn_study.webp',
    );
    expect(
      const Article(slug: 'other', title: 't', excerpt: '', urls: {}).image,
      isNull,
    );
  });

  test('the study links to the journal and keeps the not-therapy line', () {
    final en = Article.ownStudy(AppLocalizationsEn());
    final de = Article.ownStudy(AppLocalizationsDe());

    for (final brightness in Brightness.values) {
      expect(en.urlFor(brightness), _studyUrl);
    }
    expect(en.excerpt, contains('is not therapy'));
    expect(de.excerpt, contains('keine Therapie'));
  });
}
