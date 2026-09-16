"""Bounded in-process rate limiting.

Scope and limits, stated plainly: this is a fixed window held in one process's
memory. It is **per process**, so N API workers permit up to N times the limit;
it resets on restart; and it is not shared across containers or machines. It
exists to bound accidental retry storms and trivial scripted abuse against the
auth and provisioning routes without adding Redis or another service. It is not
a security control against a distributed attacker, and it is not a quota system.
"""

import threading
from collections import deque
from time import monotonic


class FixedWindowLimiter:
    def __init__(self, limit: int, window_seconds: float = 60.0, max_keys: int = 4096) -> None:
        self._limit = max(1, limit)
        self._window = window_seconds
        self._max_keys = max_keys
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> bool:
        """Record an attempt. False means the caller is over the limit."""
        now = monotonic()
        with self._lock:
            if len(self._hits) > self._max_keys:
                # Bounded memory: drop keys whose windows have fully expired.
                self._hits = {
                    k: v for k, v in self._hits.items() if v and now - v[-1] < self._window
                }
                if len(self._hits) > self._max_keys:
                    self._hits.clear()
            window = self._hits.setdefault(key, deque())
            while window and now - window[0] >= self._window:
                window.popleft()
            if len(window) >= self._limit:
                return False
            window.append(now)
            return True
