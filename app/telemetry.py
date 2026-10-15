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
SERVED_FROM_BLOCKED = "blocked"  # quota/rate-limit rejection; never a successful query
SERVED_FROM_ERROR = "error"  # provider/bridge failure; nothing delivered, nothing charged

REVENUE_NONE = "none"
REVENUE_SIMULATED = "simulated"
REVENUE_PENDING = "pending"  # payment verified, settlement not yet confirmed
REVENUE_SETTLED = "settled"  # settlement confirmed successful by the facilitator
REVENUE_FAILED = "failed"  # settlement confirmed failed; no revenue


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
    credit_cost_usd: Decimal | None,
    now: datetime | None = None,
) -> QueryCostRecord:
    """Never invents a cost.

    - Zero Helius calls -> cost is exactly 0 (always a valid fact,
      regardless of whether a credit rate is configured).
    - Any unknown-credit attempt -> cost is None (unknown), even if other
      attempts in the same query had confirmed/estimated credits.
    - Known (confirmed/estimated) credits but NO configured credit rate
      (`credit_cost_usd is None`) -> cost is None (unknown), never 0.
      A positive credit count priced at "0" because nobody configured a
      rate would make margin look artificially profitable.
    """

    now = now or datetime.now(timezone.utc)

    if usage.zero_helius_calls:
        cost: Decimal | None = Decimal("0")
    elif usage.credits_unknown:
        cost = None
    elif credit_cost_usd is None:
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
    credit_cost_usd: Decimal | None,
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
            # Additive migration: on-chain payer identity and settlement tx
            # (public data from the facilitator's settlement response). The
            # payer address is the only authenticated customer identity a
            # paid call has; X-Agent-Id remains an unauthenticated label.
            existing = {row[1] for row in conn.execute("PRAGMA table_info(query_costs)")}
            for column, kind in (("payer", "TEXT"), ("settlement_tx", "TEXT"),
                                 ("latency_ms", "REAL"), ("http_status", "INTEGER")):
                if column not in existing:
                    conn.execute(f"ALTER TABLE query_costs ADD COLUMN {column} {kind}")

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

    def finalize_revenue(
        self,
        request_id: str,
        *,
        revenue_kind: str,
        revenue_usd: Decimal | None,
        payer: str | None = None,
        settlement_tx: str | None = None,
    ) -> bool:
        """Updates a PENDING row once real x402 settlement is confirmed
        (success or failure) by the middleware -- never by the route
        handler itself, which runs before settlement happens.

        Only transitions a row that is still PENDING: a no-op (returns
        False) if no row exists for this request_id (verification itself
        failed, so the route never ran), or if the row was already
        finalized to something else (e.g. REVENUE_NONE for a mainnet
        no-charge decision) -- that must never be overwritten to FAILED
        just because the HTTP response happened to be non-200.
        """

        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT cost_usd_estimated FROM query_costs WHERE request_id = ? AND revenue_kind = ?",
                (request_id, REVENUE_PENDING),
            ).fetchone()
            if row is None:
                return False
            cost = row["cost_usd_estimated"]
            margin = (
                str(Decimal(revenue_usd) - Decimal(cost))
                if revenue_usd is not None and cost is not None
                else None
            )
            conn.execute(
                "UPDATE query_costs SET revenue_usd = ?, revenue_kind = ?, margin_usd_estimated = ?, "
                "payer = COALESCE(?, payer), settlement_tx = COALESCE(?, settlement_tx) "
                "WHERE request_id = ? AND revenue_kind = ?",
                (
                    str(revenue_usd) if revenue_usd is not None else None,
                    revenue_kind,
                    margin,
                    payer,
                    settlement_tx,
                    request_id,
                    REVENUE_PENDING,
                ),
            )
            return True

    def set_outcome(self, request_id: str, *, latency_ms: float, http_status: int) -> None:
        """End-to-end latency and final HTTP status, written after the
        response is complete (so it includes settlement time)."""
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE query_costs SET latency_ms = ?, http_status = ? WHERE request_id = ?",
                (round(latency_ms, 1), http_status, request_id),
            )

    def purge_older_than(self, cutoff_iso: str) -> int:
        """Retention: deletes rows older than `cutoff_iso` (ISO-8601,
        UTC). Returns the number of rows removed. Callers decide the
        retention window; this module does not invent a default."""

        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM query_costs WHERE timestamp < ?", (cutoff_iso,))
            return cur.rowcount

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


_BUDGET_SCHEMA = """
CREATE TABLE IF NOT EXISTS budget_state (
    scope TEXT PRIMARY KEY,
    day TEXT NOT NULL,
    reserved_credits INTEGER NOT NULL DEFAULT 0,
    spent_credits INTEGER NOT NULL DEFAULT 0
);
"""


class ReservationHandle:
    """Yielded by `BudgetGuard.reserve()`. Three ways a reservation can
    end, and the budget accounting is deliberately asymmetric between
    them:

    - `.commit(actual_credits)`: the call completed and its real cost is
      known. Moves exactly `actual_credits` from reserved into spent.
    - `.cancel()`: the call definitely never reached Helius (e.g. input
      validation failed before any request was sent). Safe to roll back
      the full reservation with zero spend.
    - Neither called (the `with` block exits some other way, typically
      an exception during the RPC call itself): AMBIGUOUS, not a free
      pass. A timeout or connection error does not prove Helius never
      billed the attempt. The full originally-reserved amount is charged
      as spent -- conservative, so the tracked daily budget can never
      understate real usage because of an unconfirmed outcome. Call
      `.cancel()` explicitly whenever "definitely free" is actually
      known, rather than relying on this default.
    """

    def __init__(self) -> None:
        self.actual_credits: int | None = None
        self.cancelled: bool = False

    def commit(self, actual_credits: int) -> None:
        self.actual_credits = actual_credits

    def cancel(self) -> None:
        self.cancelled = True


class BudgetGuard:
    """Daily global credit budget + per-query credit cap + concurrency cap
    on in-flight Helius calls.

    `reserve()` is atomic: the check (per-query limit, daily budget
    including what is already *reserved* by other in-flight calls, and
    concurrency) and the reservation itself happen under the same lock
    and the same SQLite transaction, so concurrent callers cannot both
    pass a check that only one of them should pass (the bug this class
    previously had: `check_query` ran outside the lock that incremented
    `_in_flight`, and the daily spend was only updated after the fact by
    a separate, uncoordinated `record_spend`).

    Credits (`reserved_credits`/`spent_credits`) are persisted in SQLite
    (same ledger file as `CostLedger`, keyed by `scope`) so a restart or
    redeploy cannot silently reset the daily budget to zero. In-flight
    concurrency is intentionally kept in-process: a fresh process has no
    calls genuinely in flight, so resetting it on restart is correct, not
    a gap.

    `allow_unknown_credits=False` (the default) means an estimate of
    `None` is refused outright in anything that claims to be a real
    budget -- the audited risk is a free/cheap "unknown-cost" call
    silently bypassing every cap.
    """

    def __init__(
        self,
        *,
        ledger_db_path: str,
        daily_budget_credits: int | None,
        per_query_credit_limit: int | None,
        max_concurrent_calls: int | None = None,
        allow_unknown_credits: bool = False,
        scope: str = "global",
        day_provider: Callable[[], str] | None = None,
    ):
        self._daily_budget = daily_budget_credits
        self._per_query_limit = per_query_credit_limit
        self._max_concurrent = max_concurrent_calls
        self._allow_unknown = allow_unknown_credits
        self._scope = scope
        self._day_provider = day_provider or (lambda: datetime.now(timezone.utc).date().isoformat())
        self._lock = Lock()
        self._in_flight = 0
        self._db_path = ledger_db_path
        self._persistent_conn: sqlite3.Connection | None = None
        if self._db_path == ":memory:":
            self._persistent_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._persistent_conn.row_factory = sqlite3.Row
        with self._lock, self._connect() as conn:
            conn.executescript(_BUDGET_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        if self._persistent_conn is not None:
            return self._persistent_conn
        conn = sqlite3.connect(self._db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def _state_locked(self, conn: sqlite3.Connection) -> tuple[str, int, int]:
        today = self._day_provider()
        row = conn.execute(
            "SELECT day, reserved_credits, spent_credits FROM budget_state WHERE scope = ?",
            (self._scope,),
        ).fetchone()
        if row is None or row["day"] != today:
            conn.execute(
                "INSERT INTO budget_state (scope, day, reserved_credits, spent_credits) "
                "VALUES (?, ?, 0, 0) "
                "ON CONFLICT(scope) DO UPDATE SET day = excluded.day, "
                "reserved_credits = 0, spent_credits = 0",
                (self._scope, today),
            )
            return today, 0, 0
        return row["day"], row["reserved_credits"], row["spent_credits"]

    def _check_locked(self, conn: sqlite3.Connection, estimated_credits: int | None) -> BudgetDecision:
        if estimated_credits is None and not self._allow_unknown:
            return BudgetDecision(False, "unknown_credits_not_allowed")
        if (
            self._per_query_limit is not None
            and estimated_credits is not None
            and estimated_credits > self._per_query_limit
        ):
            return BudgetDecision(False, "per_query_credit_limit_exceeded")
        _, reserved, spent = self._state_locked(conn)
        if self._daily_budget is not None:
            projected = spent + reserved + (estimated_credits or 0)
            if projected > self._daily_budget:
                return BudgetDecision(False, "daily_budget_exhausted")
        if self._max_concurrent is not None and self._in_flight >= self._max_concurrent:
            return BudgetDecision(False, "max_concurrent_calls_reached")
        return BudgetDecision(True)

    def check_query(self, estimated_credits: int | None) -> BudgetDecision:
        with self._lock, self._connect() as conn:
            return self._check_locked(conn, estimated_credits)

    @contextmanager
    def reserve(self, estimated_credits: int | None = None) -> Iterator[ReservationHandle]:
        reserved_amount = estimated_credits or 0
        with self._lock, self._connect() as conn:
            decision = self._check_locked(conn, estimated_credits)
            if not decision.allowed:
                raise BudgetExceeded(decision.reason or "budget_exceeded")
            self._state_locked(conn)  # ensures the day row exists/rolled over
            conn.execute(
                "UPDATE budget_state SET reserved_credits = reserved_credits + ? WHERE scope = ?",
                (reserved_amount, self._scope),
            )
            self._in_flight += 1

        handle = ReservationHandle()
        try:
            yield handle
        finally:
            if handle.actual_credits is not None:
                actual = handle.actual_credits  # confirmed real cost
            elif handle.cancelled:
                actual = 0  # confirmed never reached Helius
            else:
                # Ambiguous exit (e.g. an exception during the RPC call):
                # charge the full reservation rather than assume zero.
                actual = reserved_amount
            with self._lock, self._connect() as conn:
                self._in_flight -= 1
                conn.execute(
                    "UPDATE budget_state SET reserved_credits = reserved_credits - ?, "
                    "spent_credits = spent_credits + ? WHERE scope = ?",
                    (reserved_amount, actual, self._scope),
                )

    def record_spend(self, credits: int) -> None:
        """Direct spend outside of `reserve()` (e.g. backfilling a known
        cost). Prefer `reserve()` + `handle.commit()` for real call sites
        so the daily budget check accounts for in-flight reservations."""

        with self._lock, self._connect() as conn:
            self._state_locked(conn)
            conn.execute(
                "UPDATE budget_state SET spent_credits = spent_credits + ? WHERE scope = ?",
                (credits, self._scope),
            )

    @property
    def spent_today(self) -> int:
        with self._lock, self._connect() as conn:
            _, _, spent = self._state_locked(conn)
            return spent

    @property
    def reserved_now(self) -> int:
        with self._lock, self._connect() as conn:
            _, reserved, _ = self._state_locked(conn)
            return reserved

    @property
    def in_flight(self) -> int:
        with self._lock:
            return self._in_flight
