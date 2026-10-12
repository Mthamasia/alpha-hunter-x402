"""In-memory TTL cache and fixed-window rate limiter for the free preview
endpoint.

Deliberately dependency-free (no Redis): this process runs as a single
uvicorn worker (see Dockerfile). State is per-process, not shared across
replicas; that limitation is documented in README.md rather than hidden.

Both classes accept an injectable `clock` callable so tests never need a
real `time.sleep`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Callable, Hashable


@dataclass
class TTLCache:
    ttl_seconds: float
    clock: Callable[[], float] = time.monotonic
    _store: dict[Hashable, tuple[object, float]] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def get(self, key: Hashable):
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if self.clock() >= expires_at:
                del self._store[key]
                return None
            return value

    def set(self, key: Hashable, value: object) -> None:
        with self._lock:
            self._store[key] = (value, self.clock() + self.ttl_seconds)

    def __len__(self) -> int:
        return len(self._store)


@dataclass
class FixedWindowRateLimiter:
    """Simple fixed-window counter per key (e.g. client IP).

    Not exact (bursts across a window boundary can briefly exceed the
    limit), but correct enough for abuse protection on a free endpoint,
    and trivially testable with an injected clock.
    """

    max_requests: int
    window_seconds: float
    clock: Callable[[], float] = time.monotonic
    _store: dict[Hashable, tuple[float, int]] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def allow(self, key: Hashable) -> bool:
        now = self.clock()
        with self._lock:
            window_start, count = self._store.get(key, (now, 0))
            if now - window_start >= self.window_seconds:
                window_start, count = now, 0
            if count >= self.max_requests:
                self._store[key] = (window_start, count)
                return False
            self._store[key] = (window_start, count + 1)
            return True
