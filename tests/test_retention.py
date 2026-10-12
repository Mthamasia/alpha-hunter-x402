import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.quota import TIER_ANONYMOUS, QuotaStore
from app.retention import periodic_purge_loop
from app.telemetry import (
    REVENUE_NONE,
    ROUTE_PREVIEW,
    SERVED_FROM_CACHE,
    CostLedger,
    HeliusCallTracker,
    new_request_id,
    record_query,
)


def test_periodic_purge_loop_actually_removes_old_data():
    async def scenario():
        quota_store = QuotaStore(":memory:")
        quota_store.check_and_consume("ip:aaa", TIER_ANONYMOUS, "MINT-A")
        assert quota_store.purge_expired() == {
            "quota_minute": 0,
            "quota_day": 0,
            "quota_mints_seen": 0,
        }  # nothing old yet; sanity check before the loop runs

        cost_ledger = CostLedger(":memory:")
        record_query(
            cost_ledger,
            tracker=HeliusCallTracker(),
            request_id=new_request_id(),
            route=ROUTE_PREVIEW,
            chain="solana",
            mint="MINT-OLD",
            agent_id=None,
            served_from=SERVED_FROM_CACHE,
            revenue_usd=None,
            revenue_kind=REVENUE_NONE,
            credit_cost_usd=None,
            now=datetime.now(timezone.utc) - timedelta(days=200),
        )
        assert len(cost_ledger.all_records()) == 1

        task = asyncio.create_task(
            periodic_purge_loop(
                quota_store, cost_ledger, interval_seconds=0.01, telemetry_retention_days=90
            )
        )
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # The 200-day-old row is older than the 90-day retention window.
        assert cost_ledger.all_records() == []

    asyncio.run(scenario())


def test_periodic_purge_loop_survives_an_iteration_that_raises():
    async def scenario():
        quota_store = QuotaStore(":memory:")
        cost_ledger = CostLedger(":memory:")

        calls = {"n": 0}
        real_purge = quota_store.purge_expired

        def flaky_purge():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("simulated storage hiccup")
            return real_purge()

        quota_store.purge_expired = flaky_purge

        task = asyncio.create_task(
            periodic_purge_loop(
                quota_store, cost_ledger, interval_seconds=0.01, telemetry_retention_days=90
            )
        )
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # The loop kept running (and calling purge again) after the first
        # iteration's exception instead of dying silently.
        assert calls["n"] >= 2

    asyncio.run(scenario())
