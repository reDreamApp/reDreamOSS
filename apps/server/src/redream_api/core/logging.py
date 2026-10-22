"""Logging setup. Plain stdout — captured by `docker compose logs`."""

from __future__ import annotations

import logging
import sys

from .config import settings


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())
