"""In-memory TTL cache and fixed-window rate limiter.

Deliberately dependency-free (no Redis): this process runs as a single
uvicorn worker (see Dockerfile). State is per-process, not shared across
replicas or across a restart -- that limitation is documented in
README.md rather than hidden.

These two classes are now the *response cache* and a best-effort extra
layer only. The actual free-tier quota enforcement (persistent, atomic,
bounded by design since it is keyed by day/minute buckets rather than
one entry per caller forever) lives in `app/quota.py`. Both classes here
are bounded (`max_entries`, LRU eviction) because an unbounded dict keyed
by caller IP/mint is itself a memory-exhaustion vector under a scan.

Both classes accept an injectable `clock` callable so tests never need a
real `time.sleep`.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from threading import Lock
from typing import Callable, Hashable

DEFAULT_MAX_ENTRIES = 10_000


class TTLCache:
    def __init__(
        self,
        ttl_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ):
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self.max_entries = max_entries
        self._store: "OrderedDict[Hashable, tuple[object, float]]" = OrderedDict()
        self._lock = Lock()

    def get(self, key: Hashable):
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if self.clock() >= expires_at:
                del self._store[key]
                return None
            self._store.move_to_end(key)
            return value

    def set(self, key: Hashable, value: object) -> None:
        with self._lock:
            self._store[key] = (value, self.clock() + self.ttl_seconds)
            self._store.move_to_end(key)
            while len(self._store) > self.max_entries:
                self._store.popitem(last=False)  # evict the oldest entry

    def __len__(self) -> int:
        return len(self._store)


class FixedWindowRateLimiter:
    """Simple fixed-window counter per key (e.g. client IP).

    Not exact (bursts across a window boundary can briefly exceed the
    limit), but correct enough for a best-effort extra layer in front of
    `app/quota.py`'s persistent enforcement, and trivially testable with
    an injected clock. Bounded: a key not seen again is eventually
    evicted rather than retained forever.
    """

    def __init__(
        self,
        max_requests: int,
        window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.clock = clock
        self.max_entries = max_entries
        self._store: "OrderedDict[Hashable, tuple[float, int]]" = OrderedDict()
        self._lock = Lock()

    def allow(self, key: Hashable) -> bool:
        now = self.clock()
        with self._lock:
            window_start, count = self._store.get(key, (now, 0))
            if now - window_start >= self.window_seconds:
                window_start, count = now, 0
            allowed = count < self.max_requests
            if allowed:
                count += 1
            self._store[key] = (window_start, count)
            self._store.move_to_end(key)
            while len(self._store) > self.max_entries:
                self._store.popitem(last=False)
            return allowed

    def __len__(self) -> int:
        return len(self._store)
