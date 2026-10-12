"""Per-query cost/revenue telemetry.

This module never calls Helius itself. It instruments *around* a future
real RPC call site (`HeliusCallTracker`) so that when selective on-demand
updates are implemented, the exact same plumbing used and tested here
starts recording real attempts with zero code change at the call site.

Today, every request served by this API is answered either from the
in-process preview cache or from the read-only Alpha Hunter X bridge
(SQLite via HTTP) -- never Helius directly. That is recorded as
`helius_call_count == 0`, which is the only situation allowed to produce
a cost of exactly "0" (see `build_record`). Any attempt whose credits are
neither confirmed nor estimated is tracked as UNKNOWN and never priced
at zero -- cost becomes `None` (unknown), per the "never invent a cost"
requirement.

Storage: a dedicated SQLite file (`Settings.telemetry_db_path`), always
separate from the Alpha Hunter intelligence database -- this service
never opens that database directly in the first place; it only talks to
the bridge over HTTP.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from threading import Lock
from typing import Callable, Iterator

CONFIRMED = "confirmed"
ESTIMATED = "estimated"
UNKNOWN = "unknown"

ROUTE_PREVIEW = "preview"
ROUTE_INVESTIGATE = "investigate"

SERVED_FROM_CACHE = "cache"
SERVED_FROM_BRIDGE = "bridge"
SERVED_FROM_STUB = "stub"

REVENUE_NONE = "none"
REVENUE_SIMULATED = "simulated"
REVENUE_REAL = "real"


class BudgetExceeded(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# --------------------------------------------------------------- Helius call tracking


@dataclass
class HeliusCallAttempt:
    method: str
    attempt_number: int
    outcome: str  # "success" | "error"
    duration_ms: float
    credits_confirmed: int | None = None
    credits_estimated: int | None = None
    error: str | None = None

    @property
    def credit_basis(self) -> str:
        if self.credits_confirmed is not None:
            return CONFIRMED
        if self.credits_estimated is not None:
            return ESTIMATED
        return UNKNOWN


@dataclass
class _AttemptHandle:
    credits_confirmed: int | None = None
    credits_estimated: int | None = None

    def mark_credits(self, *, confirmed: int | None = None, estimated: int | None = None) -> None:
        if confirmed is not None:
            self.credits_confirmed = confirmed
        if estimated is not None:
            self.credits_estimated = estimated


@dataclass
class HeliusUsageSummary:
    call_count: int
    retry_count: int
    credits_confirmed_total: int | None
    credits_estimated_total: int | None
    credits_unknown: bool
    duration_ms_total: float

    @property
    def zero_helius_calls(self) -> bool:
        return self.call_count == 0


class HeliusCallTracker:
    """Wraps zero or more attempts of ONE logical Helius RPC call
    (retries included) for a single query. Nothing in this class makes
    a network call; it only measures whatever the caller does inside
    `with tracker.attempt(method) as a:`.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self.calls: list[HeliusCallAttempt] = []

    @contextmanager
    def attempt(self, method: str) -> Iterator[_AttemptHandle]:
        number = len(self.calls) + 1
        start = self._clock()
        handle = _AttemptHandle()
        try:
            yield handle
        except Exception as exc:
            self.calls.append(
                HeliusCallAttempt(
                    method=method,
                    attempt_number=number,
                    outcome="error",
                    duration_ms=(self._clock() - start) * 1000,
                    credits_confirmed=handle.credits_confirmed,
                    credits_estimated=handle.credits_estimated,
                    error=str(exc)[:200],
                )
            )
            raise
        else:
            self.calls.append(
                HeliusCallAttempt(
                    method=method,
                    attempt_number=number,
                    outcome="success",
                    duration_ms=(self._clock() - start) * 1000,
                    credits_confirmed=handle.credits_confirmed,
                    credits_estimated=handle.credits_estimated,
                )
            )

    def summary(self) -> HeliusUsageSummary:
        confirmed_total: int | None = None
        estimated_total: int | None = None
        unknown = False
        duration_total = 0.0
        for call in self.calls:
            duration_total += call.duration_ms
            if call.credits_confirmed is not None:
                confirmed_total = (confirmed_total or 0) + call.credits_confirmed
            elif call.credits_estimated is not None:
                estimated_total = (estimated_total or 0) + call.credits_estimated
            else:
                unknown = True
        return HeliusUsageSummary(
            call_count=len(self.calls),
            retry_count=max(0, len(self.calls) - 1),
            credits_confirmed_total=confirmed_total,
            credits_estimated_total=estimated_total,
            credits_unknown=unknown,
            duration_ms_total=duration_total,
        )


# --------------------------------------------------------------- cost record


@dataclass
class QueryCostRecord:
    request_id: str
    timestamp: str
    route: str
    chain: str
    mint: str
    agent_id: str | None
    served_from: str
    helius_call_count: int
    helius_retry_count: int
    helius_credits_confirmed_total: int | None
    helius_credits_estimated_total: int | None
    helius_credits_unknown: bool
    helius_duration_ms_total: float
    revenue_usd: str | None
    revenue_kind: str
    cost_usd_estimated: str | None
    margin_usd_estimated: str | None
    raw_calls_json: str = "[]"


def new_request_id() -> str:
    return str(uuid.uuid4())


def build_record(
    *,
    request_id: str,
    route: str,
    chain: str,
    mint: str,
    agent_id: str | None,
    served_from: str,
    usage: HeliusUsageSummary,
    revenue_usd: Decimal | None,
    revenue_kind: str,
    credit_cost_usd: Decimal,
    now: datetime | None = None,
) -> QueryCostRecord:
    """Never invents a cost: zero Helius calls -> cost is exactly 0.
    Any unknown-credit attempt -> cost is None (unknown), even if other
    attempts in the same query had confirmed/estimated credits.
    """

    now = now or datetime.now(timezone.utc)

    if usage.zero_helius_calls:
        cost: Decimal | None = Decimal("0")
    elif usage.credits_unknown:
        cost = None
    else:
        known_credits = (usage.credits_confirmed_total or 0) + (usage.credits_estimated_total or 0)
        cost = Decimal(known_credits) * credit_cost_usd

    margin = revenue_usd - cost if (revenue_usd is not None and cost is not None) else None

    return QueryCostRecord(
        request_id=request_id,
        timestamp=now.isoformat().replace("+00:00", "Z"),
        route=route,
        chain=chain,
        mint=mint,
        agent_id=agent_id,
        served_from=served_from,
        helius_call_count=usage.call_count,
        helius_retry_count=usage.retry_count,
        helius_credits_confirmed_total=usage.credits_confirmed_total,
        helius_credits_estimated_total=usage.credits_estimated_total,
        helius_credits_unknown=usage.credits_unknown,
        helius_duration_ms_total=usage.duration_ms_total,
        revenue_usd=str(revenue_usd) if revenue_usd is not None else None,
        revenue_kind=revenue_kind,
        cost_usd_estimated=str(cost) if cost is not None else None,
        margin_usd_estimated=str(margin) if margin is not None else None,
        # `HeliusUsageSummary` is a pure aggregate (no raw calls); callers
        # that want the raw per-attempt audit trail should go through
        # `record_query` below, which fills this in from the tracker.
    )


def record_query(
    ledger: "CostLedger",
    *,
    tracker: HeliusCallTracker,
    request_id: str,
    route: str,
    chain: str,
    mint: str,
    agent_id: str | None,
    served_from: str,
    revenue_usd: Decimal | None,
    revenue_kind: str,
    credit_cost_usd: Decimal,
    now: datetime | None = None,
) -> QueryCostRecord:
    """Builds a record (with the full raw-calls audit trail) and persists
    it. This is the function route handlers should call."""

    usage = tracker.summary()
    record = build_record(
        request_id=request_id,
        route=route,
        chain=chain,
        mint=mint,
        agent_id=agent_id,
        served_from=served_from,
        usage=usage,
        revenue_usd=revenue_usd,
        revenue_kind=revenue_kind,
        credit_cost_usd=credit_cost_usd,
        now=now,
    )
    record.raw_calls_json = json.dumps([asdict(c) for c in tracker.calls])
    ledger.record(record)
    return record


# --------------------------------------------------------------- ledger storage


_SCHEMA = """
CREATE TABLE IF NOT EXISTS query_costs (
    request_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    route TEXT NOT NULL,
    chain TEXT NOT NULL,
    mint TEXT NOT NULL,
    agent_id TEXT,
    served_from TEXT NOT NULL,
    helius_call_count INTEGER NOT NULL,
    helius_retry_count INTEGER NOT NULL,
    helius_credits_confirmed_total INTEGER,
    helius_credits_estimated_total INTEGER,
    helius_credits_unknown INTEGER NOT NULL,
    helius_duration_ms_total REAL NOT NULL,
    revenue_usd TEXT,
    revenue_kind TEXT NOT NULL,
    cost_usd_estimated TEXT,
    margin_usd_estimated TEXT,
    raw_calls_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_query_costs_mint ON query_costs(mint);
CREATE INDEX IF NOT EXISTS idx_query_costs_agent ON query_costs(agent_id);
CREATE INDEX IF NOT EXISTS idx_query_costs_ts ON query_costs(timestamp);
"""

_COLUMNS = (
    "request_id", "timestamp", "route", "chain", "mint", "agent_id", "served_from",
    "helius_call_count", "helius_retry_count",
    "helius_credits_confirmed_total", "helius_credits_estimated_total",
    "helius_credits_unknown", "helius_duration_ms_total",
    "revenue_usd", "revenue_kind", "cost_usd_estimated", "margin_usd_estimated",
    "raw_calls_json",
)


class CostLedger:
    """Dedicated SQLite store for query economics. Always a separate file
    from the Alpha Hunter intelligence database; this service never opens
    that database directly (it only speaks HTTP to the read-only bridge).

    `db_path=":memory:"` (the default -- see Settings.telemetry_db_path)
    keeps one persistent connection alive for the ledger's lifetime, since
    a fresh `sqlite3.connect(":memory:")` per call would otherwise start
    from an empty database every time. Any real file path reconnects per
    call, same as before.
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._lock = Lock()
        self._persistent_conn: sqlite3.Connection | None = None
        if self._db_path == ":memory:":
            self._persistent_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._persistent_conn.row_factory = sqlite3.Row
        with self._lock, self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        # `Connection.__enter__`/`__exit__` only commit/rollback the
        # transaction -- they never close the connection -- so reusing the
        # same object across calls here is safe for the in-memory case.
        if self._persistent_conn is not None:
            return self._persistent_conn
        conn = sqlite3.connect(self._db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def record(self, entry: QueryCostRecord) -> None:
        values = tuple(
            int(getattr(entry, col)) if col == "helius_credits_unknown" else getattr(entry, col)
            for col in _COLUMNS
        )
        placeholders = ",".join("?" for _ in _COLUMNS)
        with self._lock, self._connect() as conn:
            conn.execute(
                f"INSERT INTO query_costs ({','.join(_COLUMNS)}) VALUES ({placeholders})",
                values,
            )

    def all_records(self) -> list[sqlite3.Row]:
        with self._lock, self._connect() as conn:
            return conn.execute("SELECT * FROM query_costs ORDER BY timestamp").fetchall()

    def totals_by_mint(self, mint: str) -> dict:
        return self._aggregate("mint = ?", (mint,))

    def totals_by_agent(self, agent_id: str) -> dict:
        return self._aggregate("agent_id = ?", (agent_id,))

    def totals_by_period(self, start_iso: str, end_iso: str) -> dict:
        return self._aggregate("timestamp >= ? AND timestamp < ?", (start_iso, end_iso))

    def totals_all(self) -> dict:
        return self._aggregate("1=1", ())

    def _aggregate(self, where: str, params: tuple) -> dict:
        with self._lock, self._connect() as conn:
            rows = conn.execute(f"SELECT * FROM query_costs WHERE {where}", params).fetchall()
        return _summarize(rows)


def _summarize(rows: list[sqlite3.Row]) -> dict:
    query_count = len(rows)
    helius_calls_total = sum(r["helius_call_count"] for r in rows)
    helius_retries_total = sum(r["helius_retry_count"] for r in rows)
    credits_confirmed = sum(
        r["helius_credits_confirmed_total"] for r in rows if r["helius_credits_confirmed_total"] is not None
    )
    credits_estimated = sum(
        r["helius_credits_estimated_total"] for r in rows if r["helius_credits_estimated_total"] is not None
    )
    any_unknown = any(r["helius_credits_unknown"] for r in rows)
    revenue_total = sum(Decimal(r["revenue_usd"]) for r in rows if r["revenue_usd"] is not None)

    cost_total: Decimal | None
    if any(r["cost_usd_estimated"] is None and r["helius_call_count"] > 0 for r in rows):
        # At least one query has unknown cost -- never sum a partial total
        # as if it were complete.
        cost_total = None
    else:
        cost_total = sum(Decimal(r["cost_usd_estimated"]) for r in rows if r["cost_usd_estimated"] is not None)

    margin_total = (revenue_total - cost_total) if cost_total is not None else None

    return {
        "query_count": query_count,
        "helius_calls_total": helius_calls_total,
        "helius_retries_total": helius_retries_total,
        "helius_credits_confirmed_total": credits_confirmed,
        "helius_credits_estimated_total": credits_estimated,
        "helius_credits_unknown_present": any_unknown,
        "revenue_usd_total": str(revenue_total),
        "cost_usd_estimated_total": str(cost_total) if cost_total is not None else None,
        "margin_usd_estimated_total": str(margin_total) if margin_total is not None else None,
    }


# --------------------------------------------------------------- budget guard


@dataclass
class BudgetDecision:
    allowed: bool
    reason: str | None = None


class BudgetGuard:
    """Daily global credit budget + per-query credit cap + concurrency cap
    on in-flight Helius calls. `None` means "not configured" (no cap).

    Nothing in this stage spends real credits -- there is no real Helius
    call site yet. This guard exists so a future selective on-demand
    update feature can call `reserve()` around a real RPC call on day
    one, instead of bolting budget enforcement on after the fact.
    """

    def __init__(
        self,
        *,
        daily_budget_credits: int | None,
        per_query_credit_limit: int | None,
        max_concurrent_calls: int | None = None,
        clock: Callable[[], float] = time.monotonic,
        day_provider: Callable[[], str] | None = None,
    ):
        self._daily_budget = daily_budget_credits
        self._per_query_limit = per_query_credit_limit
        self._max_concurrent = max_concurrent_calls
        self._clock = clock
        self._day_provider = day_provider or (lambda: datetime.now(timezone.utc).date().isoformat())
        self._lock = Lock()
        self._current_day: str | None = None
        self._spent_today = 0
        self._in_flight = 0

    def _roll_day_if_needed_locked(self) -> None:
        day = self._day_provider()
        if day != self._current_day:
            self._current_day = day
            self._spent_today = 0

    def check_query(self, estimated_credits: int | None) -> BudgetDecision:
        with self._lock:
            self._roll_day_if_needed_locked()
            if (
                self._per_query_limit is not None
                and estimated_credits is not None
                and estimated_credits > self._per_query_limit
            ):
                return BudgetDecision(False, "per_query_credit_limit_exceeded")
            if self._daily_budget is not None:
                projected = self._spent_today + (estimated_credits or 0)
                if projected > self._daily_budget:
                    return BudgetDecision(False, "daily_budget_exhausted")
            if self._max_concurrent is not None and self._in_flight >= self._max_concurrent:
                return BudgetDecision(False, "max_concurrent_calls_reached")
            return BudgetDecision(True)

    @contextmanager
    def reserve(self, estimated_credits: int | None = None) -> Iterator[None]:
        decision = self.check_query(estimated_credits)
        if not decision.allowed:
            raise BudgetExceeded(decision.reason or "budget_exceeded")
        with self._lock:
            self._in_flight += 1
        try:
            yield
        finally:
            with self._lock:
                self._in_flight -= 1

    def record_spend(self, credits: int) -> None:
        with self._lock:
            self._roll_day_if_needed_locked()
            self._spent_today += credits

    @property
    def spent_today(self) -> int:
        with self._lock:
            self._roll_day_if_needed_locked()
            return self._spent_today

    @property
    def in_flight(self) -> int:
        with self._lock:
            return self._in_flight
