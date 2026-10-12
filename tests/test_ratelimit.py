from app.ratelimit import FixedWindowRateLimiter, TTLCache


class FakeClock:
    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_ttl_cache_hit_before_expiry_miss_after():
    clock = FakeClock()
    cache = TTLCache(ttl_seconds=10, clock=clock)

    cache.set("mint-1", "value-1")
    assert cache.get("mint-1") == "value-1"

    clock.advance(9.999)
    assert cache.get("mint-1") == "value-1"

    clock.advance(0.001)
    assert cache.get("mint-1") is None
    assert len(cache) == 0


def test_ttl_cache_miss_for_unknown_key():
    cache = TTLCache(ttl_seconds=10, clock=FakeClock())
    assert cache.get("never-set") is None


def test_fixed_window_limiter_allows_up_to_max_then_blocks():
    clock = FakeClock()
    limiter = FixedWindowRateLimiter(max_requests=2, window_seconds=60, clock=clock)

    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("1.2.3.4") is False

    # A different key has its own independent counter.
    assert limiter.allow("5.6.7.8") is True


def test_fixed_window_limiter_resets_after_window():
    clock = FakeClock()
    limiter = FixedWindowRateLimiter(max_requests=1, window_seconds=60, clock=clock)

    assert limiter.allow("k") is True
    assert limiter.allow("k") is False

    clock.advance(60)
    assert limiter.allow("k") is True


def test_ttl_cache_is_bounded_and_evicts_oldest_under_a_large_scan():
    clock = FakeClock()
    cache = TTLCache(ttl_seconds=3600, clock=clock, max_entries=100)

    for i in range(10_000):
        cache.set(f"mint-{i}", i)

    assert len(cache) == 100
    # The earliest-inserted keys were evicted; the most recent survive.
    assert cache.get("mint-0") is None
    assert cache.get("mint-9999") == 9999


def test_fixed_window_limiter_is_bounded_under_a_large_ip_scan():
    clock = FakeClock()
    limiter = FixedWindowRateLimiter(max_requests=10, window_seconds=60, clock=clock, max_entries=50)

    for i in range(10_000):
        limiter.allow(f"203.0.113.{i}")

    assert len(limiter) == 50
