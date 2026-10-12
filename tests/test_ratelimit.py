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
