import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:redream_app/core/settings/app_settings.dart';
import 'package:redream_app/core/theme/app_theme.dart';
import 'package:redream_app/core/utils/errors.dart';
import 'package:redream_app/core/widgets/app_button.dart';
import 'package:redream_app/core/widgets/brand_logo.dart';
import 'package:redream_app/core/widgets/feedback.dart';
import 'package:redream_app/features/settings/profile.dart';
import 'package:redream_app/features/settings/profile_labels.dart';
import 'package:redream_app/features/settings/profile_state.dart';
import 'package:redream_app/l10n/generated/app_localizations.dart';

class OnboardingScreen extends StatefulWidget {
  const OnboardingScreen({super.key});

  @override
  State<OnboardingScreen> createState() => _OnboardingScreenState();
}

class _OnboardingScreenState extends State<OnboardingScreen> {
  static const _pageCount = 5;

  final _controller = PageController();
  int _page = 0;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _go(int page) => _controller.animateToPage(
    page,
    duration: const Duration(milliseconds: 300),
    curve: Curves.easeOut,
  );

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final settings = context.read<AppSettings>();
    final isLast = _page == _pageCount - 1;
    return Scaffold(
      body: SafeArea(
        child: Column(
          children: [
            Align(
              alignment: Alignment.centerRight,
              child: TextButton(
                onPressed: settings.completeOnboarding,
                child: Text(l10n.skip),
              ),
            ),
            Expanded(
              child: PageView(
                controller: _controller,
                onPageChanged: (i) => setState(() => _page = i),
                children: [
                  const _WelcomePage(),
                  _InfoPage(
                    title: l10n.onboardingScienceTitle,
                    body: l10n.onboardingScienceBody,
                    image: 'assets/images/onboarding_science.png',
                  ),
                  _InfoPage(
                    title: l10n.onboardingAiTitle,
                    body: l10n.onboardingAiBody,
                    image: 'assets/images/onboarding_ai.png',
                  ),
                  _InfoPage(
                    title: l10n.onboardingPrivacyTitle,
                    body: l10n.onboardingPrivacyBody,
                    image: 'assets/images/onboarding_privacy.png',
                  ),
                  const _ProfilePage(),
                ],
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(8, 4, 8, 12),
              child: Row(
                children: [
                  // Hidden, not removed, on the first/last page so the
                  // dots stay centred.
                  Visibility.maintain(
                    visible: _page > 0,
                    child: IconButton(
                      onPressed: () => _go(_page - 1),
                      icon: const Icon(Icons.arrow_back),
                    ),
                  ),
                  const Spacer(),
                  _Dots(count: _pageCount, current: _page),
                  const Spacer(),
                  Visibility.maintain(
                    visible: !isLast,
                    child: IconButton(
                      onPressed: () => _go(_page + 1),
                      icon: const Icon(Icons.arrow_forward),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// "Welcome to" reads on into the logo, which carries the app's name.
class _WelcomePage extends StatelessWidget {
  const _WelcomePage();

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return SingleChildScrollView(
      padding: _pagePadding,
      child: Column(
        children: [
          const SizedBox(height: 32),
          _Title(l10n.onboardingWelcomeTitle),
          const SizedBox(height: 8),
          const BrandLogo(height: 220),
          const SizedBox(height: 24),
          _Body(l10n.onboardingWelcomeBody),
        ],
      ),
    );
  }
}

/// A picture on the home screen's gradient, a title and a short text.
class _InfoPage extends StatelessWidget {
  const _InfoPage({
    required this.title,
    required this.body,
    required this.image,
  });

  final String title;
  final String body;
  final String image;

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      padding: _pagePadding,
      child: Column(
        children: [
          Container(
            width: double.infinity,
            padding: const EdgeInsets.all(20),
            decoration: BoxDecoration(
              gradient: BrandTheme.of(context).heroGradient,
              borderRadius: BorderRadius.circular(20),
            ),
            child: Image.asset(image, height: 200, excludeFromSemantics: true),
          ),
          const SizedBox(height: 32),
          _Title(title),
          const SizedBox(height: 12),
          _Body(body),
        ],
      ),
    );
  }
}

const _pagePadding = EdgeInsets.fromLTRB(24, 8, 24, 16);

class _Title extends StatelessWidget {
  const _Title(this.text);

  final String text;

  @override
  Widget build(BuildContext context) => Text(
    text,
    textAlign: TextAlign.center,
    style: Theme.of(
      context,
    ).textTheme.headlineMedium?.copyWith(fontWeight: FontWeight.w700),
  );
}

class _Body extends StatelessWidget {
  const _Body(this.text);

  final String text;

  @override
  Widget build(BuildContext context) => Text(
    text,
    textAlign: TextAlign.center,
    style: Theme.of(context).textTheme.bodyLarge?.copyWith(
      color: BrandTheme.of(context).muted,
      height: 1.5,
    ),
  );
}

class _ProfilePage extends StatefulWidget {
  const _ProfilePage();

  @override
  State<_ProfilePage> createState() => _ProfilePageState();
}

class _ProfilePageState extends State<_ProfilePage> {
  String? _gender;
  String? _ageRange;
  String? _frequency;
  bool _busy = false;
  String? _error;

  Future<void> _submit() async {
    final l10n = AppLocalizations.of(context)!;
    final profile = context.read<ProfileState>();
    final settings = context.read<AppSettings>();
    final answers = Profile(
      gender: _gender,
      ageRange: _ageRange,
      nightmareFrequency: _frequency,
    );
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      // Every answer is optional; with none chosen there is nothing to save.
      if (!answers.isEmpty) await profile.save(answers);
      await settings.completeOnboarding();
    } on Exception catch (e) {
      if (mounted) setState(() => _error = describeError(e, l10n));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  DropdownButtonFormField<String> _dropdown({
    required String label,
    required List<String> values,
    required String? value,
    required ValueChanged<String?> onChanged,
    required AppLocalizations l10n,
  }) => DropdownButtonFormField<String>(
    initialValue: value,
    // Long choices shrink to the field instead of overflowing it.
    isExpanded: true,
    decoration: InputDecoration(labelText: label),
    items: [
      for (final v in values)
        DropdownMenuItem(value: v, child: Text(profileLabel(v, l10n))),
    ],
    onChanged: onChanged,
  );

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return SingleChildScrollView(
      padding: _pagePadding,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const SizedBox(height: 32),
          _Title(l10n.onboardingProfileTitle),
          const SizedBox(height: 12),
          _Body(l10n.onboardingProfileHint),
          const SizedBox(height: 32),
          _dropdown(
            label: l10n.profileGender,
            values: Profile.genders,
            value: _gender,
            onChanged: (v) => setState(() => _gender = v),
            l10n: l10n,
          ),
          const SizedBox(height: 16),
          _dropdown(
            label: l10n.profileAgeRange,
            values: Profile.ageRanges,
            value: _ageRange,
            onChanged: (v) => setState(() => _ageRange = v),
            l10n: l10n,
          ),
          const SizedBox(height: 16),
          _dropdown(
            label: l10n.profileNightmareFrequency,
            values: Profile.nightmareFrequencies,
            value: _frequency,
            onChanged: (v) => setState(() => _frequency = v),
            l10n: l10n,
          ),
          const SizedBox(height: 24),
          if (_error != null) ...[
            InlineNotice(message: _error!),
            const SizedBox(height: 16),
          ],
          AppButton(
            label: l10n.onboardingSubmit,
            style: AppButtonStyle.gradient,
            busy: _busy,
            onPressed: _submit,
          ),
        ],
      ),
    );
  }
}

class _Dots extends StatelessWidget {
  const _Dots({required this.count, required this.current});

  final int count;
  final int current;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        for (var i = 0; i < count; i++)
          AnimatedContainer(
            duration: const Duration(milliseconds: 200),
            margin: const EdgeInsets.symmetric(horizontal: 3),
            width: i == current ? 18 : 8,
            height: 8,
            decoration: BoxDecoration(
              color: i == current ? scheme.primary : scheme.outline,
              borderRadius: BorderRadius.circular(4),
            ),
          ),
      ],
    );
  }
}
