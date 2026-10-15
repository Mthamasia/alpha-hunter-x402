from decimal import Decimal

import pytest

from app.telemetry import (
    REVENUE_NONE,
    REVENUE_SETTLED,
    ROUTE_INVESTIGATE,
    ROUTE_PREVIEW,
    SERVED_FROM_BRIDGE,
    SERVED_FROM_CACHE,
    BudgetExceeded,
    BudgetGuard,
    CostLedger,
    HeliusCallTracker,
    build_record,
    new_request_id,
    record_query,
)


# ---------------------------------------------------------------- HeliusCallTracker


def test_zero_helius_calls_cost_is_zero():
    tracker = HeliusCallTracker()
    usage = tracker.summary()

    assert usage.zero_helius_calls
    record = build_record(
        request_id="r1",
        route=ROUTE_PREVIEW,
        chain="solana",
        mint="MINT",
        agent_id=None,
        served_from=SERVED_FROM_CACHE,
        usage=usage,
        revenue_usd=None,
        revenue_kind=REVENUE_NONE,
        credit_cost_usd=Decimal("0.001"),
    )

    assert record.helius_call_count == 0
    assert record.cost_usd_estimated == "0"
    assert record.margin_usd_estimated is None  # no revenue on preview


def test_unknown_credits_never_priced_as_zero():
    tracker = HeliusCallTracker()
    with tracker.attempt("getAsset"):
        pass  # never calls mark_credits -> unknown

    usage = tracker.summary()
    assert usage.credits_unknown
    assert usage.call_count == 1

    record = build_record(
        request_id="r2",
        route=ROUTE_INVESTIGATE,
        chain="solana",
        mint="MINT",
        agent_id=None,
        served_from=SERVED_FROM_BRIDGE,
        usage=usage,
        revenue_usd=Decimal("0.05"),
        revenue_kind=REVENUE_SETTLED,
        credit_cost_usd=Decimal("0.001"),
    )

    assert record.cost_usd_estimated is None
    assert record.margin_usd_estimated is None  # cost unknown -> margin unknown


def test_known_credits_without_a_configured_rate_is_unknown_cost_not_zero():
    """The audit's blocker #3: a positive, confirmed credit count must
    never be priced as 0 just because nobody configured
    HELIUS_CREDIT_COST_USD. Only an EXPLICIT rate (including an explicit
    0) may price it."""

    tracker = HeliusCallTracker()
    with tracker.attempt("getAsset") as a:
        a.mark_credits(confirmed=100)

    usage = tracker.summary()
    assert not usage.credits_unknown  # the credits themselves ARE known

    record = build_record(
        request_id="r-rate-unconfigured",
        route=ROUTE_INVESTIGATE,
        chain="solana",
        mint="MINT",
        agent_id=None,
        served_from=SERVED_FROM_BRIDGE,
        usage=usage,
        revenue_usd=Decimal("0.05"),
        revenue_kind=REVENUE_SETTLED,
        credit_cost_usd=None,  # not configured
    )

    assert record.cost_usd_estimated is None
    assert record.margin_usd_estimated is None


def test_explicit_zero_rate_is_honored_as_a_real_zero_cost():
    tracker = HeliusCallTracker()
    with tracker.attempt("getAsset") as a:
        a.mark_credits(confirmed=100)

    record = build_record(
        request_id="r-rate-zero",
        route=ROUTE_INVESTIGATE,
        chain="solana",
        mint="MINT",
        agent_id=None,
        served_from=SERVED_FROM_BRIDGE,
        usage=tracker.summary(),
        revenue_usd=Decimal("0.05"),
        revenue_kind=REVENUE_SETTLED,
        credit_cost_usd=Decimal("0"),  # explicitly configured as free
    )

    assert record.cost_usd_estimated == "0"
    assert record.margin_usd_estimated == "0.05"


def test_confirmed_and_estimated_credits_summed_and_priced():
    tracker = HeliusCallTracker()
    with tracker.attempt("getAsset") as a:
        a.mark_credits(confirmed=5)
    with tracker.attempt("getTransaction") as a:
        a.mark_credits(estimated=3)

    usage = tracker.summary()
    assert usage.credits_confirmed_total == 5
    assert usage.credits_estimated_total == 3
    assert not usage.credits_unknown

    record = build_record(
        request_id="r3",
        route=ROUTE_INVESTIGATE,
        chain="solana",
        mint="MINT",
        agent_id="agent-1",
        served_from=SERVED_FROM_BRIDGE,
        usage=usage,
        revenue_usd=Decimal("0.05"),
        revenue_kind=REVENUE_SETTLED,
        credit_cost_usd=Decimal("0.001"),
    )

    assert record.cost_usd_estimated == "0.008"
    assert record.margin_usd_estimated == "0.042"


def test_retry_count_tracked_including_errors():
    tracker = HeliusCallTracker()

    for _ in range(2):
        with pytest.raises(RuntimeError):
            with tracker.attempt("getAsset"):
                raise RuntimeError("transient")

    with tracker.attempt("getAsset") as a:
        a.mark_credits(confirmed=1)

    usage = tracker.summary()
    assert usage.call_count == 3
    assert usage.retry_count == 2
    assert [c.outcome for c in tracker.calls] == ["error", "error", "success"]
    assert usage.credits_confirmed_total == 1  # only the successful attempt billed


# ---------------------------------------------------------------- CostLedger


def test_record_query_raw_calls_json_round_trips():
    import json

    ledger = CostLedger(":memory:")
    tracker = HeliusCallTracker()
    with tracker.attempt("getAsset") as a:
        a.mark_credits(confirmed=2)

    record_query(
        ledger,
        tracker=tracker,
        request_id=new_request_id(),
        route=ROUTE_INVESTIGATE,
        chain="solana",
        mint="MINT",
        agent_id="agent-1",
        served_from=SERVED_FROM_BRIDGE,
        revenue_usd=Decimal("0.05"),
        revenue_kind=REVENUE_SETTLED,
        credit_cost_usd=Decimal("0.001"),
    )

    rows = ledger.all_records()
    assert len(rows) == 1
    calls = json.loads(rows[0]["raw_calls_json"])
    assert len(calls) == 1
    assert calls[0]["method"] == "getAsset"
    assert calls[0]["credits_confirmed"] == 2


def test_ledger_aggregates_by_mint_agent_and_period():
    ledger = CostLedger(":memory:")

    def record(mint, agent, revenue, confirmed_credits=None):
        tracker = HeliusCallTracker()
        if confirmed_credits is not None:
            with tracker.attempt("getAsset") as a:
                a.mark_credits(confirmed=confirmed_credits)
        record_query(
            ledger,
            tracker=tracker,
            request_id=new_request_id(),
            route=ROUTE_INVESTIGATE,
            chain="solana",
            mint=mint,
            agent_id=agent,
            served_from=SERVED_FROM_BRIDGE,
            revenue_usd=revenue,
            revenue_kind=REVENUE_SETTLED if revenue is not None else REVENUE_NONE,
            credit_cost_usd=Decimal("0.001"),
        )

    record("MINT-A", "agent-1", Decimal("0.05"))
    record("MINT-A", "agent-2", Decimal("0.05"))
    record("MINT-B", "agent-1", Decimal("0.05"), confirmed_credits=10)

    by_mint = ledger.totals_by_mint("MINT-A")
    assert by_mint["query_count"] == 2
    assert by_mint["helius_calls_total"] == 0
    assert by_mint["revenue_usd_total"] == "0.10"
    assert by_mint["cost_usd_estimated_total"] == "0"
    assert by_mint["margin_usd_estimated_total"] == "0.10"

    by_agent = ledger.totals_by_agent("agent-1")
    assert by_agent["query_count"] == 2
    assert by_agent["helius_calls_total"] == 1
    assert by_agent["cost_usd_estimated_total"] == "0.010"

    totals = ledger.totals_all()
    assert totals["query_count"] == 3
    assert totals["revenue_usd_total"] == "0.15"


def test_ledger_never_sums_a_partial_total_when_cost_is_unknown():
    ledger = CostLedger(":memory:")

    tracker = HeliusCallTracker()
    with tracker.attempt("getAsset"):
        pass  # unknown credits

    record_query(
        ledger,
        tracker=tracker,
        request_id=new_request_id(),
        route=ROUTE_INVESTIGATE,
        chain="solana",
        mint="MINT-C",
        agent_id=None,
        served_from=SERVED_FROM_BRIDGE,
        revenue_usd=Decimal("0.05"),
        revenue_kind=REVENUE_SETTLED,
        credit_cost_usd=Decimal("0.001"),
    )

    totals = ledger.totals_by_mint("MINT-C")
    assert totals["helius_credits_unknown_present"] is True
    assert totals["cost_usd_estimated_total"] is None
    assert totals["margin_usd_estimated_total"] is None
    # Revenue is still known and summed even when cost is not.
    assert totals["revenue_usd_total"] == "0.05"


def test_purge_older_than_removes_only_rows_before_cutoff():
    ledger = CostLedger(":memory:")

    for ts in ("2026-01-01T00:00:00Z", "2026-06-01T00:00:00Z", "2026-10-01T00:00:00Z"):
        record_query(
            ledger,
            tracker=HeliusCallTracker(),
            request_id=new_request_id(),
            route=ROUTE_PREVIEW,
            chain="solana",
            mint="MINT",
            agent_id=None,
            served_from=SERVED_FROM_CACHE,
            revenue_usd=None,
            revenue_kind=REVENUE_NONE,
            credit_cost_usd=None,
            now=__import__("datetime").datetime.fromisoformat(ts.replace("Z", "+00:00")),
        )

    removed = ledger.purge_older_than("2026-06-01T00:00:00Z")
    assert removed == 1
    remaining = sorted(r["timestamp"] for r in ledger.all_records())
    assert remaining == ["2026-06-01T00:00:00Z", "2026-10-01T00:00:00Z"]


def test_ledger_db_path_is_separate_from_intelligence_database(tmp_path):
    db_path = tmp_path / "telemetry.db"
    ledger = CostLedger(str(db_path))
    record_query(
        ledger,
        tracker=HeliusCallTracker(),
        request_id=new_request_id(),
        route=ROUTE_PREVIEW,
        chain="solana",
        mint="MINT",
        agent_id=None,
        served_from=SERVED_FROM_CACHE,
        revenue_usd=None,
        revenue_kind=REVENUE_NONE,
        credit_cost_usd=Decimal("0"),
    )

    assert db_path.exists()
    assert db_path.name != "alpha_hunter.db"
    # It is a plain SQLite file with only the telemetry schema.
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert tables == {"query_costs"}


# ---------------------------------------------------------------- BudgetGuard


class FakeClock:
    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_budget_guard_rejects_unknown_credits_by_default():
    guard = BudgetGuard(ledger_db_path=":memory:", daily_budget_credits=10, per_query_credit_limit=None)

    decision = guard.check_query(None)
    assert not decision.allowed
    assert decision.reason == "unknown_credits_not_allowed"


def test_budget_guard_blocks_when_daily_budget_exhausted():
    guard = BudgetGuard(ledger_db_path=":memory:", daily_budget_credits=10, per_query_credit_limit=None)

    assert guard.check_query(5).allowed
    guard.record_spend(5)
    assert guard.check_query(5).allowed  # 5 + 5 == 10, within budget
    guard.record_spend(5)

    decision = guard.check_query(1)
    assert not decision.allowed
    assert decision.reason == "daily_budget_exhausted"


def test_budget_guard_blocks_per_query_limit_regardless_of_daily_spend():
    guard = BudgetGuard(ledger_db_path=":memory:", daily_budget_credits=1000, per_query_credit_limit=5)

    decision = guard.check_query(6)
    assert not decision.allowed
    assert decision.reason == "per_query_credit_limit_exceeded"

    assert guard.check_query(5).allowed


def test_budget_guard_reserve_blocks_daily_budget_from_in_flight_reservations_not_just_spend():
    """The bug this guard previously had: a second concurrent reservation
    could pass `check_query` because only confirmed `spent` counted, not
    what a first in-flight call had already reserved."""

    guard = BudgetGuard(ledger_db_path=":memory:", daily_budget_credits=10, per_query_credit_limit=None)

    with guard.reserve(estimated_credits=8) as handle:
        assert guard.reserved_now == 8
        decision = guard.check_query(5)  # 8 reserved + 5 would be 13 > 10
        assert not decision.allowed
        assert decision.reason == "daily_budget_exhausted"
        with pytest.raises(BudgetExceeded):
            with guard.reserve(estimated_credits=5):
                pass
        handle.cancel()  # this attempt definitely never reached Helius

    assert guard.reserved_now == 0
    assert guard.spent_today == 0  # explicitly cancelled -> real zero cost, not a guess


def test_budget_guard_reserve_commit_moves_reserved_into_spent():
    guard = BudgetGuard(ledger_db_path=":memory:", daily_budget_credits=10, per_query_credit_limit=None)

    with guard.reserve(estimated_credits=8) as handle:
        assert guard.reserved_now == 8
        handle.commit(actual_credits=6)  # actual RPC cost less than the estimate

    assert guard.reserved_now == 0
    assert guard.spent_today == 6


def test_budget_guard_reserve_charges_full_reservation_on_an_ambiguous_exception():
    """P2 from the independent audit: a timeout/error during the RPC call
    does not prove Helius never billed the attempt. The guard must not
    assume zero cost just because the outcome is unknown -- it charges
    the full reservation (the conservative upper bound) instead."""

    guard = BudgetGuard(ledger_db_path=":memory:", daily_budget_credits=10, per_query_credit_limit=None)

    with pytest.raises(RuntimeError):
        with guard.reserve(estimated_credits=8):
            raise RuntimeError("the RPC call blew up before any spend was known")

    assert guard.reserved_now == 0
    assert guard.spent_today == 8  # charged conservatively, not rolled back to 0
    assert guard.in_flight == 0


def test_budget_guard_reserve_cancel_is_the_only_way_to_get_a_real_zero_cost():
    guard = BudgetGuard(ledger_db_path=":memory:", daily_budget_credits=10, per_query_credit_limit=None)

    with guard.reserve(estimated_credits=8) as handle:
        handle.cancel()  # e.g. input validation failed before any request was sent

    assert guard.reserved_now == 0
    assert guard.spent_today == 0


def test_budget_guard_reserve_forgetting_to_commit_also_charges_conservatively():
    """Not just exceptions: simply forgetting to call `.commit()` or
    `.cancel()` must not default to a free pass either."""

    guard = BudgetGuard(ledger_db_path=":memory:", daily_budget_credits=10, per_query_credit_limit=None)

    with guard.reserve(estimated_credits=8):
        pass  # neither commit() nor cancel() called

    assert guard.spent_today == 8


def test_budget_guard_blocks_max_concurrent_calls():
    guard = BudgetGuard(
        ledger_db_path=":memory:",
        daily_budget_credits=None,
        per_query_credit_limit=None,
        max_concurrent_calls=1,
        allow_unknown_credits=True,
    )

    with guard.reserve():
        assert guard.in_flight == 1
        decision = guard.check_query(None)
        assert not decision.allowed
        assert decision.reason == "max_concurrent_calls_reached"
        with pytest.raises(BudgetExceeded):
            with guard.reserve():
                pass

    assert guard.in_flight == 0
    with guard.reserve():
        assert guard.in_flight == 1


def test_budget_guard_resets_daily_spend_on_new_day():
    days = iter(["2026-10-12", "2026-10-12", "2026-10-13"])
    guard = BudgetGuard(
        ledger_db_path=":memory:",
        daily_budget_credits=10,
        per_query_credit_limit=None,
        day_provider=lambda: next(days),
    )

    guard.record_spend(8)
    assert guard.spent_today == 8  # still 2026-10-12

    assert guard.spent_today == 0  # rolled over to 2026-10-13


def test_budget_guard_persists_spend_across_a_simulated_restart(tmp_path):
    db_path = str(tmp_path / "telemetry.db")

    guard_before_restart = BudgetGuard(ledger_db_path=db_path, daily_budget_credits=10, per_query_credit_limit=None)
    guard_before_restart.record_spend(7)

    # A brand-new BudgetGuard instance, same file -- simulates a Railway
    # redeploy picking the process back up.
    guard_after_restart = BudgetGuard(ledger_db_path=db_path, daily_budget_credits=10, per_query_credit_limit=None)

    assert guard_after_restart.spent_today == 7
    decision = guard_after_restart.check_query(4)  # 7 + 4 == 11 > 10
    assert not decision.allowed
    assert decision.reason == "daily_budget_exhausted"


def test_budget_guard_reserve_is_atomic_under_thread_concurrency():
    """20 threads each try to reserve 1 credit against a budget of 10.
    Exactly 10 must succeed; the rest must be rejected by the guard, not
    by a race that lets the total exceed the budget."""

    import threading

    guard = BudgetGuard(ledger_db_path=":memory:", daily_budget_credits=10, per_query_credit_limit=None)
    successes = []
    lock = threading.Lock()

    def worker():
        try:
            with guard.reserve(estimated_credits=1) as handle:
                with lock:
                    successes.append(1)
                handle.commit(actual_credits=1)
        except BudgetExceeded:
            pass

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(successes) == 10
    assert guard.spent_today == 10
