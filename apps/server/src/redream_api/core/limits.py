"""In-memory rate limits for the auth routes and the daily AI-request cap.

Per process: the server runs as exactly one uvicorn worker, so these counters
see every request. They reset on restart.

Over a limit the answer is 429 with Retry-After (whole seconds). The app tells
the two apart by the detail: DAILY_LIMIT_REACHED shows its daily-limit banner,
TOO_MANY_ATTEMPTS a sign-in message.
"""

from __future__ import annotations

import ipaddress
import math
import time
from collections import OrderedDict, deque
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException, Request, status

from .config import settings

TOO_MANY_ATTEMPTS = "too many attempts, try again later"
DAILY_LIMIT_REACHED = "daily limit reached"

# Keys tracked per limiter: random emails or addresses must not grow memory
# without bound. Past it the least recently used key is forgotten.
MAX_KEYS = 10_000


def _monotonic() -> float:
    return time.monotonic()


def _utcnow() -> datetime:
    return datetime.now(UTC)


def too_many(retry_after: int, detail: str = TOO_MANY_ATTEMPTS) -> HTTPException:
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS, detail, headers={"Retry-After": str(retry_after)}
    )


def client_ip(request: Request) -> str:
    """The rate-limit key of the peer address uvicorn reports: the client from
    X-Forwarded-For when the peer is in FORWARDED_ALLOW_IPS (the image runs
    uvicorn with --proxy-headers; compose.yaml trusts its own network: Caddy
    and the Docker proxy of its 127.0.0.1-only port), else the peer itself.
    uvicorn takes the rightmost X-Forwarded-For entry that is not a trusted
    proxy, so a client cannot choose it by sending the header itself, whether
    the proxy in front sets or appends to it. IPv6 addresses count per
    /64: one host usually holds a whole /64 and could otherwise rotate
    addresses past the per-IP limits."""
    host = request.client.host if request.client else "unknown"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return str(address.ipv4_mapped)
        return str(ipaddress.ip_network(f"{address}/64", strict=False))
    return str(address)


class RateLimiter:
    """At most `limit` recorded events per key within a sliding `window`.

    A limit on failures reserves the attempt with `reserve()` before awaiting
    the password check and `forgive()`s it on success: concurrent requests then
    count while they are still in flight. Checking first and recording after
    the await would let a whole burst through before its first failure counts.
    """

    def __init__(self, limit: int, window: timedelta, *, max_keys: int = MAX_KEYS) -> None:
        self.limit = limit
        self.window = window.total_seconds()
        self.max_keys = max_keys
        self._events: OrderedDict[str, deque[float]] = OrderedDict()

    def retry_after(self, key: str) -> int | None:
        """Seconds until `key` may act again; None while it is under the limit."""
        events = self._live(key)
        if events is None or len(events) < self.limit:
            return None
        # A slot frees when the event that keeps the count at the limit ages out.
        oldest_counted = events[len(events) - self.limit]
        return max(1, math.ceil(oldest_counted + self.window - _monotonic()))

    def check(self, key: str) -> None:
        """Raise 429 when `key` is at the limit."""
        retry_after = self.retry_after(key)
        if retry_after is not None:
            raise too_many(retry_after)

    def record(self, key: str) -> float:
        """Count one event for `key` now; returns its stamp (for `forgive`)."""
        events = self._live(key)
        if events is None:
            events = self._events[key] = deque()
        stamp = _monotonic()
        events.append(stamp)
        self._events.move_to_end(key)
        while len(self._events) > self.max_keys:
            self._events.popitem(last=False)
        return stamp

    def reserve(self, key: str) -> float:
        """`check` and `record` in one step, with no await in between; 429 at
        the limit. Returns the stamp to `forgive` when the attempt succeeds."""
        self.check(key)
        return self.record(key)

    def forgive(self, key: str, stamp: float) -> None:
        """Take back the event `stamp` of `key` (a reserved attempt that
        succeeded). A no-op when it already aged out or was evicted."""
        events = self._events.get(key)
        if events is None:
            return
        try:
            events.remove(stamp)
        except ValueError:
            return
        if not events:
            del self._events[key]

    def reset(self) -> None:
        self._events.clear()

    def __len__(self) -> int:
        return len(self._events)

    def _live(self, key: str) -> deque[float] | None:
        """`key`'s events inside the window (older ones dropped), None if none."""
        events = self._events.get(key)
        if events is None:
            return None
        horizon = _monotonic() - self.window
        while events and events[0] <= horizon:
            events.popleft()
        if not events:
            del self._events[key]
            return None
        return events


# Login: failed attempts per normalized email, and every attempt per client IP.
login_failures_by_email = RateLimiter(10, timedelta(minutes=15))
login_attempts_by_ip = RateLimiter(30, timedelta(minutes=15))
# Registration: every attempt per client IP.
register_attempts_by_ip = RateLimiter(5, timedelta(hours=1))
# Password and email change: failed attempts per user.
account_failures_by_user = RateLimiter(10, timedelta(minutes=15))

_LIMITERS = (
    login_failures_by_email,
    login_attempts_by_ip,
    register_attempts_by_ip,
    account_failures_by_user,
)


class DailyCap:
    """Requests per user per UTC day. Only today's counters are kept."""

    def __init__(self) -> None:
        self._day: date | None = None
        self._counts: dict[UUID, int] = {}

    def admit(self, user_id: UUID, limit: int) -> None:
        """Count one request for `user_id`; 429 once `limit` is used up today.
        A limit of 0 is off. A request counts when admitted, even if it fails
        later."""
        if limit <= 0:
            return
        now = _utcnow()
        today = now.date()
        if today != self._day:
            self._day = today
            self._counts = {}
        used = self._counts.get(user_id, 0)
        if used >= limit:
            midnight = datetime.combine(today + timedelta(days=1), datetime.min.time(), UTC)
            raise too_many(
                max(1, math.ceil((midnight - now).total_seconds())), DAILY_LIMIT_REACHED
            )
        self._counts[user_id] = used + 1

    def reset(self) -> None:
        self._day = None
        self._counts = {}


daily_ai_requests = DailyCap()


def count_ai_request(user_id: UUID) -> None:
    """Admit one chat turn, transcription or rehearsal rendering for `user_id`
    under DAILY_AI_REQUESTS_PER_USER; 429 "daily limit reached" over it."""
    daily_ai_requests.admit(user_id, settings.daily_ai_requests_per_user)


def reset() -> None:
    """Forget every counter (tests)."""
    for limiter in _LIMITERS:
        limiter.reset()
    daily_ai_requests.reset()
