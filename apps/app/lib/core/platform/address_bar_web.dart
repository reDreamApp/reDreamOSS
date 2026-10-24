import 'package:web/web.dart' as web;

/// Drops the `#...` part from the address bar without reloading, so the
/// personal key is neither left on screen nor saved in a bookmark.
void clearAddressFragment() {
  final location = web.window.location;
  web.window.history.replaceState(
    null,
    '',
    '${location.pathname}${location.search}',
  );
}
