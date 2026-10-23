"""Logging setup. Plain stdout — captured by `docker compose logs`."""

from __future__ import annotations

import logging
import sys

from .config import settings

# The signed audio route (services/audio_links): its path names a user and
# its query string is the authorization, so the access log shows neither.
_AUDIO_FILES_PATH = "/api/v1/audio/files/"


class _RedactAudioLinks(logging.Filter):
    """uvicorn's access log line, with an audio file's key and signature cut.

    Matched anywhere in the path, not as a prefix: behind a root_path the
    logged path starts with that prefix."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) > 2:
            path = args[2]
            if isinstance(path, str) and _AUDIO_FILES_PATH in path:
                kept = path[: path.index(_AUDIO_FILES_PATH) + len(_AUDIO_FILES_PATH)]
                record.args = (*args[:2], kept + "…", *args[3:])
        return True


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, _RedactAudioLinks) for f in access.filters):
        access.addFilter(_RedactAudioLinks())
