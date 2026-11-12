"""In-memory login throttle: per client IP and per normalised username.

Limits: MAX_FAILURES failed attempts inside a sliding WINDOW_SECONDS window.
State lives in this process only. It is NOT shared between workers or hosts and
is lost on restart. See docs/SECURITY.md.
"""

from __future__ import annotations

import math
import threading
import time
from collections import defaultdict
from typing import Callable

MAX_FAILURES = 5
WINDOW_SECONDS = 300
_KEY_MAX_LEN = 64


def normalize_username(username: str) -> str:
    """Case-insensitive, trimmed, length-capped key so spelling variants share one bucket."""
    return username.strip().casefold()[:_KEY_MAX_LEN]


class LoginThrottle:
    def __init__(
        self,
        max_failures: int = MAX_FAILURES,
        window_seconds: int = WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._by_ip: dict[str, list[float]] = defaultdict(list)
        self._by_user: dict[str, list[float]] = defaultdict(list)
        self._last_sweep = clock()

    def _live(self, bucket: dict[str, list[float]], key: str, now: float) -> list[float]:
        cutoff = now - self.window_seconds
        stamps = [stamp for stamp in bucket.get(key, ()) if stamp > cutoff]
        if stamps:
            bucket[key] = stamps
        else:
            bucket.pop(key, None)
        return stamps

    def _sweep(self, now: float) -> None:
        if now - self._last_sweep < self.window_seconds:
            return
        self._last_sweep = now
        for bucket in (self._by_ip, self._by_user):
            for key in list(bucket):
                self._live(bucket, key, now)

    def begin(self, ip: str, username: str) -> tuple[float | None, int]:
        """Reserve one attempt. Returns (ticket, retry_after).

        When blocked, ticket is None and retry_after is the seconds until the
        window frees a slot. Otherwise the attempt is counted as a failure up
        front, so concurrent guesses cannot slip past the limit; call
        succeed() to take it back.
        """
        user_key = normalize_username(username)
        with self._lock:
            now = self._clock()
            self._sweep(now)
            ip_stamps = self._live(self._by_ip, ip, now)
            user_stamps = self._live(self._by_user, user_key, now)
            waits = [
                stamps[0] + self.window_seconds - now
                for stamps in (ip_stamps, user_stamps)
                if len(stamps) >= self.max_failures
            ]
            if waits:
                return None, max(1, math.ceil(max(waits)))
            self._by_ip[ip].append(now)
            self._by_user[user_key].append(now)
            return now, 0

    def succeed(self, ip: str, username: str, ticket: float) -> None:
        """Successful login: drop the username's failures and this attempt's IP count."""
        user_key = normalize_username(username)
        with self._lock:
            self._by_user.pop(user_key, None)
            stamps = self._by_ip.get(ip)
            if stamps and ticket in stamps:
                stamps.remove(ticket)
                if not stamps:
                    self._by_ip.pop(ip, None)
