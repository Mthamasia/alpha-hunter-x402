import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest

from app.quota import (
    REASON_DISTINCT_MINTS,
    REASON_PER_DAY,
    REASON_PER_MINUTE,
    REASON_STORE_UNAVAILABLE,
    TIER_ANONYMOUS,
    TIER_FREE_KEY,
    QuotaStore,
    TierLimits,
    hash_key,
)


class FakeNow:
    def __init__(self, start: datetime):
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs):
        self.now += timedelta(**kwargs)


TIGHT_LIMITS = {
    TIER_ANONYMOUS: TierLimits(per_minute=2, per_day=5, distinct_mints_per_day=2),
    TIER_FREE_KEY: TierLimits(per_minute=4, per_day=10, distinct_mints_per_day=4),
}


def make_store(**overrides):
    clock = FakeNow(datetime(2026, 10, 12, 12, 0, 0, tzinfo=timezone.utc))
    kwargs = dict(db_path=":memory:", tier_limits=TIGHT_LIMITS, now_provider=clock)
    kwargs.update(overrides)
    return QuotaStore(**kwargs), clock


def test_per_minute_limit_enforced_with_retry_after():
    store, clock = make_store()

    assert store.check_and_consume("ip:1.2.3.4", TIER_ANONYMOUS, "MINT-A").allowed
    assert store.check_and_consume("ip:1.2.3.4", TIER_ANONYMOUS, "MINT-A").allowed

    decision = store.check_and_consume("ip:1.2.3.4", TIER_ANONYMOUS, "MINT-A")
    assert not decision.allowed
    assert decision.reason == REASON_PER_MINUTE
    assert decision.retry_after_seconds == 60  # second == 0 at clock start


def test_per_minute_limit_resets_next_minute():
    store, clock = make_store()

    store.check_and_consume("ip:1.2.3.4", TIER_ANONYMOUS, "MINT-A")
    store.check_and_consume("ip:1.2.3.4", TIER_ANONYMOUS, "MINT-A")
    assert not store.check_and_consume("ip:1.2.3.4", TIER_ANONYMOUS, "MINT-A").allowed

    clock.advance(minutes=1)
    assert store.check_and_consume("ip:1.2.3.4", TIER_ANONYMOUS, "MINT-A").allowed


def test_per_day_limit_enforced_across_minutes():
    store, clock = make_store()

    # 5 requests allowed per day (anonymous), one per minute to stay under
    # the per-minute cap of 2.
    for _ in range(5):
        assert store.check_and_consume("ip:1.2.3.4", TIER_ANONYMOUS, "MINT-A").allowed
        clock.advance(minutes=1)

    decision = store.check_and_consume("ip:1.2.3.4", TIER_ANONYMOUS, "MINT-A")
    assert not decision.allowed
    assert decision.reason == REASON_PER_DAY
    assert decision.retry_after_seconds > 0


def test_distinct_mints_per_day_limit_is_scan_protection_not_request_volume():
    store, clock = make_store()

    # Repeating the SAME mint never trips the distinct-mint limit.
    for _ in range(2):
        clock.advance(minutes=1)
        assert store.check_and_consume("ip:1.2.3.4", TIER_ANONYMOUS, "MINT-A").allowed

    # A second distinct mint is still within the limit of 2.
    clock.advance(minutes=1)
    assert store.check_and_consume("ip:1.2.3.4", TIER_ANONYMOUS, "MINT-B").allowed

    # A third distinct mint exceeds distinct_mints_per_day=2.
    clock.advance(minutes=1)
    decision = store.check_and_consume("ip:1.2.3.4", TIER_ANONYMOUS, "MINT-C")
    assert not decision.allowed
    assert decision.reason == REASON_DISTINCT_MINTS


def test_subjects_have_independent_counters():
    store, _ = make_store()

    store.check_and_consume("ip:1.1.1.1", TIER_ANONYMOUS, "MINT-A")
    store.check_and_consume("ip:1.1.1.1", TIER_ANONYMOUS, "MINT-A")
    assert not store.check_and_consume("ip:1.1.1.1", TIER_ANONYMOUS, "MINT-A").allowed

    # A different subject is unaffected.
    assert store.check_and_consume("ip:2.2.2.2", TIER_ANONYMOUS, "MINT-A").allowed


def test_free_key_tier_has_higher_limits_than_anonymous():
    store, _ = make_store()

    for _ in range(4):
        assert store.check_and_consume("key:abc", TIER_FREE_KEY, "MINT-A").allowed
    assert not store.check_and_consume("key:abc", TIER_FREE_KEY, "MINT-A").allowed


def test_unknown_tier_falls_back_to_anonymous_limits_not_a_crash():
    store, _ = make_store()

    store.check_and_consume("ip:1.1.1.1", "bogus-tier", "MINT-A")
    store.check_and_consume("ip:1.1.1.1", "bogus-tier", "MINT-A")
    assert not store.check_and_consume("ip:1.1.1.1", "bogus-tier", "MINT-A").allowed


# ---------------------------------------------------------------- API keys


def test_issued_key_resolves_to_its_tier():
    store, _ = make_store()
    issued = store.issue_api_key("agent-alpha", tier=TIER_FREE_KEY)

    assert store.resolve_key_tier(issued.raw_key) == TIER_FREE_KEY


def test_unknown_key_resolves_to_none():
    store, _ = make_store()
    assert store.resolve_key_tier("not-a-real-key") is None


def test_revoked_key_no_longer_resolves():
    store, _ = make_store()
    issued = store.issue_api_key("agent-alpha")

    assert store.resolve_key_tier(issued.raw_key) == TIER_FREE_KEY
    assert store.revoke_api_key(issued.key_id) is True
    assert store.resolve_key_tier(issued.raw_key) is None


def test_revoking_already_revoked_key_is_a_no_op():
    store, _ = make_store()
    issued = store.issue_api_key("agent-alpha")
    store.revoke_api_key(issued.key_id)

    assert store.revoke_api_key(issued.key_id) is False


def test_raw_key_is_never_stored_in_plaintext(tmp_path):
    db_path = str(tmp_path / "quota.db")
    store = QuotaStore(db_path, tier_limits=TIGHT_LIMITS)
    issued = store.issue_api_key("agent-alpha")

    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT key_hash, label FROM api_keys").fetchall()
    conn.close()

    assert len(rows) == 1
    assert rows[0][0] == hash_key(issued.raw_key)
    assert issued.raw_key not in str(rows)


def test_issue_api_key_rejects_unknown_tier():
    store, _ = make_store()
    with pytest.raises(ValueError):
        store.issue_api_key("agent-alpha", tier="premium-unlimited")


# ---------------------------------------------------------------- persistence / fail-closed


def test_quota_persists_across_a_simulated_restart(tmp_path):
    db_path = str(tmp_path / "quota.db")
    clock = FakeNow(datetime(2026, 10, 12, 12, 0, 0, tzinfo=timezone.utc))

    store_before = QuotaStore(db_path, tier_limits=TIGHT_LIMITS, now_provider=clock)
    store_before.check_and_consume("ip:1.2.3.4", TIER_ANONYMOUS, "MINT-A")
    store_before.check_and_consume("ip:1.2.3.4", TIER_ANONYMOUS, "MINT-A")

    # A brand-new QuotaStore, same file -- simulates a Railway redeploy.
    store_after_restart = QuotaStore(db_path, tier_limits=TIGHT_LIMITS, now_provider=clock)
    decision = store_after_restart.check_and_consume("ip:1.2.3.4", TIER_ANONYMOUS, "MINT-A")
    assert not decision.allowed
    assert decision.reason == REASON_PER_MINUTE


def test_fails_closed_when_store_is_unavailable(monkeypatch):
    store, _ = make_store()

    def broken_connect(self):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(QuotaStore, "_connect", broken_connect)

    decision = store.check_and_consume("ip:1.2.3.4", TIER_ANONYMOUS, "MINT-A")
    assert not decision.allowed
    assert decision.reason == REASON_STORE_UNAVAILABLE


def test_check_and_consume_is_atomic_under_thread_concurrency():
    """40 threads hit the same subject/minute bucket with a limit of 2.
    Exactly 2 must be allowed -- a race in the check-then-increment would
    let more through."""

    store, _ = make_store()
    allowed = []
    lock = threading.Lock()

    def worker():
        decision = store.check_and_consume("ip:9.9.9.9", TIER_ANONYMOUS, "MINT-A")
        if decision.allowed:
            with lock:
                allowed.append(1)

    threads = [threading.Thread(target=worker) for _ in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(allowed) == 2
