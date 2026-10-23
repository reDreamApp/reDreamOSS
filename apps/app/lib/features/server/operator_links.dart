import 'package:flutter/material.dart';
import 'package:redream_app/core/widgets/feedback.dart';
import 'package:redream_app/l10n/generated/app_localizations.dart';
import 'package:url_launcher/url_launcher.dart';

/// One plain address: no list separators, no URL parts.
final _email = RegExp(r'^[^\s@/:,;<>"?#]+@[^\s@/:,;<>"?#]+\.[^\s@/:,;<>"?#]+$');

/// The operator's contact (free text from `/instance`) as a link: an email
/// address becomes mailto:, an http(s) address stays. Anything else has no
/// link and is only shown.
Uri? contactUri(String contact) {
  final text = contact.trim();
  if (_email.hasMatch(text)) return Uri(scheme: 'mailto', path: text);
  return webUri(text);
}

/// [text] when it is an http(s) address with a host, else null.
Uri? webUri(String text) {
  final uri = Uri.tryParse(text.trim());
  if (uri == null ||
      !(uri.isScheme('http') || uri.isScheme('https')) ||
      uri.host.isEmpty ||
      uri.userInfo.isNotEmpty) {
    return null;
  }
  return uri;
}

/// Opens [uri] outside the app; says so when nothing can open it.
Future<void> openOperatorLink(BuildContext context, Uri uri) async {
  final l10n = AppLocalizations.of(context)!;
  var ok = false;
  try {
    ok = await launchUrl(uri, mode: LaunchMode.externalApplication);
  } on Exception catch (_) {}
  if (!ok && context.mounted) {
    showAppSnackBar(context, l10n.contactOpenFailed, error: true);
  }
}
