"""No-op stand-ins for the tracing decorators the vendored core uses.

reDreamOSS has no tracing backend. ``core/irt_app.py`` is vendored and keeps
importing ``observe`` and ``langfuse_context`` from here; both do nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def observe(*args: Any, **kwargs: Any) -> Any:
    """Identity decorator, usable as ``@observe`` and ``@observe(...)``."""
    if len(args) == 1 and callable(args[0]) and not kwargs:
        return args[0]

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        return func

    return decorator


class _NoOpContext:
    def update_current_trace(self, **kwargs: Any) -> None:
        return None

    def update_current_observation(self, **kwargs: Any) -> None:
        return None


langfuse_context = _NoOpContext()
