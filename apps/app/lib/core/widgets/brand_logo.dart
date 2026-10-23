import 'package:flutter/material.dart';
import 'package:redream_app/core/theme/app_theme.dart';

/// The cloud with the app's name under it. The name is text, not part of
/// the picture, so it follows the app's name.
class BrandLogo extends StatelessWidget {
  const BrandLogo({super.key, this.height = 120});

  final double height;

  @override
  Widget build(BuildContext context) => SizedBox(
    height: height,
    child: Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Expanded(child: BrandMark(size: height * 0.72)),
        SizedBox(height: height * 0.04),
        DefaultTextStyle.merge(
          style: TextStyle(
            fontSize: height * 0.18,
            fontWeight: FontWeight.w700,
            height: 1,
          ),
          child: const BrandWordmark(),
        ),
      ],
    ),
  );
}

/// The cloud without the wordmark, for places too small to read the name.
class BrandMark extends StatelessWidget {
  const BrandMark({super.key, this.size = 34});

  final double size;

  @override
  Widget build(BuildContext context) {
    final dark = Theme.of(context).brightness == Brightness.dark;
    return Image.asset(
      dark
          ? 'assets/images/logo_mark_dark.png'
          : 'assets/images/logo_mark_light.png',
      width: size,
      height: size,
      excludeFromSemantics: true,
    );
  }
}

/// The app's name set like the logo's wordmark: "re" and "OSS" in the
/// logo's turquoise, "Dream" in the text colour (black on light, white on
/// dark).
class BrandWordmark extends StatelessWidget {
  const BrandWordmark({super.key});

  @override
  Widget build(BuildContext context) {
    const accent = TextStyle(color: BrandColors.logo);
    return const Text.rich(
      TextSpan(
        children: [
          TextSpan(text: 're', style: accent),
          TextSpan(text: 'Dream'),
          TextSpan(text: 'OSS', style: accent),
        ],
      ),
      maxLines: 1,
      overflow: TextOverflow.ellipsis,
    );
  }
}
