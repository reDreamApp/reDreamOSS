{{flutter_js}}
{{flutter_build_config}}

// Everything the page loads comes from the server that serves it: the
// fonts the engine falls back to are in web/fonts/ (never fetched from
// Google), CanvasKit is bundled (--no-web-resources-cdn). No service worker:
// a new version shows on the next reload.
_flutter.loader.load({
  config: {
    fontFallbackBaseUrl: "fonts/",
  },
});
