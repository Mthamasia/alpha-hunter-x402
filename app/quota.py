"""Persistent, atomic, tiered quota enforcement for the free preview
endpoint, plus server-issued API keys.

Why not the in-memory `app/ratelimit.py` limiter for this: that state
does not survive a restart/redeploy and is not shared across workers or
replicas. This module is SQLite-backed (same persistence story as
`app/telemetry.py`) so a quota cannot be reset for free by a redeploy,
and every check-and-consume happens under one lock and one transaction
so concurrent requests cannot both slip past a boundary.

Identity: `X-Agent-Id` is a client-supplied, unauthenticated claim and
MUST NOT gate quota (see app/main.py -- it is only ever recorded in
telemetry as an unauthenticated label). The only two identities this
module recognizes are:

- An API key (`X-API-Key` header), issued by `issue_api_key` and stored
  only as a SHA-256 hash -- never in plaintext, never logged, never
  returned again after issuance.
- A resolved client IP (see `resolve_client_ip`), which by default is
  the raw transport-level peer address. Trusting a forwarded-for header
  requires explicitly configuring its exact name; nothing is trusted by
  default, since an arbitrary client-supplied header is as spoofable as
  `X-Agent-Id`.

Fail-closed: if the SQLite store itself is unavailable (disk error,
corruption, permissions), `check_and_consume` denies the request rather
than falling back to unlimited access.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Callable

logger = logging.getLogger("ahx.quota")

TIER_ANONYMOUS = "anonymous"
TIER_FREE_KEY = "free_key"

REASON_PER_MINUTE = "per_minute_limit_exceeded"
REASON_PER_DAY = "per_day_limit_exceeded"
REASON_DISTINCT_MINTS = "distinct_mints_per_day_exceeded"
REASON_STORE_UNAVAILABLE = "quota_store_unavailable"
REASON_REVOKED_KEY = "api_key_revoked_or_unknown"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    key_hash TEXT PRIMARY KEY,
    key_id TEXT UNIQUE NOT NULL,
    label TEXT,
    tier TEXT NOT NULL,
    created_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS quota_minute (
    subject_id TEXT NOT NULL,
    bucket TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (subject_id, bucket)
);
CREATE TABLE IF NOT EXISTS quota_day (
    subject_id TEXT NOT NULL,
    bucket TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (subject_id, bucket)
);
CREATE TABLE IF NOT EXISTS quota_mints_seen (
    subject_id TEXT NOT NULL,
    bucket TEXT NOT NULL,
    mint TEXT NOT NULL,
    PRIMARY KEY (subject_id, bucket, mint)
);
"""


@dataclass(frozen=True)
class TierLimits:
    per_minute: int
    per_day: int
    distinct_mints_per_day: int


DEFAULT_TIER_LIMITS: dict[str, TierLimits] = {
    TIER_ANONYMOUS: TierLimits(per_minute=10, per_day=50, distinct_mints_per_day=15),
    TIER_FREE_KEY: TierLimits(per_minute=30, per_day=300, distinct_mints_per_day=60),
}


@dataclass(frozen=True)
class QuotaDecision:
    allowed: bool
    reason: str | None = None
    retry_after_seconds: int | None = None


@dataclass(frozen=True)
class IssuedKey:
    raw_key: str  # shown to the operator exactly once; never stored
    key_id: str  # safe to log/display afterwards for revocation


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _minute_bucket(now: datetime) -> str:
    return now.strftime("%Y-%m-%dT%H:%M")


def _day_bucket(now: datetime) -> str:
    return now.strftime("%Y-%m-%d")


def _seconds_to_next_minute(now: datetime) -> int:
    return 60 - now.second


def _seconds_to_next_utc_day(now: datetime) -> int:
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((tomorrow - now).total_seconds()))


class QuotaStore:
    def __init__(
        self,
        db_path: str,
        *,
        tier_limits: dict[str, TierLimits] | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ):
        self._db_path = db_path
        self._tier_limits = tier_limits or DEFAULT_TIER_LIMITS
        self._now = now_provider or (lambda: datetime.now(timezone.utc))
        self._lock = Lock()
        self._persistent_conn: sqlite3.Connection | None = None
        if self._db_path == ":memory:":
            self._persistent_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._persistent_conn.row_factory = sqlite3.Row
        with self._lock, self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        if self._persistent_conn is not None:
            return self._persistent_conn
        conn = sqlite3.connect(self._db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    # ---------------------------------------------------------- API keys

    def issue_api_key(self, label: str, tier: str = TIER_FREE_KEY) -> IssuedKey:
        if tier not in self._tier_limits:
            raise ValueError(f"unknown tier: {tier!r}")
        raw_key = secrets.token_urlsafe(32)
        key_id = uuid.uuid4().hex[:12]
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO api_keys (key_hash, key_id, label, tier, created_at, revoked_at) "
                "VALUES (?, ?, ?, ?, ?, NULL)",
                (hash_key(raw_key), key_id, label, tier, self._now().isoformat()),
            )
        return IssuedKey(raw_key=raw_key, key_id=key_id)

    def revoke_api_key(self, key_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "UPDATE api_keys SET revoked_at = ? WHERE key_id = ? AND revoked_at IS NULL",
                (self._now().isoformat(), key_id),
            )
            return cur.rowcount > 0

    def resolve_key_tier(self, raw_key: str) -> str | None:
        """Returns the tier for a valid, non-revoked key, else None.
        `raw_key` is never logged and never stored anywhere; only its
        SHA-256 hash is looked up."""

        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT tier FROM api_keys WHERE key_hash = ? AND revoked_at IS NULL",
                (hash_key(raw_key),),
            ).fetchone()
            return row["tier"] if row is not None else None

    # ---------------------------------------------------------- quota

    def check_and_consume(self, subject_id: str, tier: str, mint: str) -> QuotaDecision:
        try:
            with self._lock, self._connect() as conn:
                return self._check_and_consume_locked(conn, subject_id, tier, mint)
        except sqlite3.Error:
            logger.exception("quota store unavailable; failing closed")
            return QuotaDecision(False, REASON_STORE_UNAVAILABLE, retry_after_seconds=30)

    def _check_and_consume_locked(
        self, conn: sqlite3.Connection, subject_id: str, tier: str, mint: str
    ) -> QuotaDecision:
        limits = self._tier_limits.get(tier, self._tier_limits[TIER_ANONYMOUS])
        now = self._now()
        minute_bucket = _minute_bucket(now)
        day_bucket = _day_bucket(now)

        minute_count = self._get_count(conn, "quota_minute", subject_id, minute_bucket)
        if minute_count >= limits.per_minute:
            return QuotaDecision(False, REASON_PER_MINUTE, _seconds_to_next_minute(now))

        day_count = self._get_count(conn, "quota_day", subject_id, day_bucket)
        if day_count >= limits.per_day:
            return QuotaDecision(False, REASON_PER_DAY, _seconds_to_next_utc_day(now))

        mint_already_seen = conn.execute(
            "SELECT 1 FROM quota_mints_seen WHERE subject_id = ? AND bucket = ? AND mint = ?",
            (subject_id, day_bucket, mint),
        ).fetchone() is not None

        if not mint_already_seen:
            distinct_count = conn.execute(
                "SELECT COUNT(*) AS n FROM quota_mints_seen WHERE subject_id = ? AND bucket = ?",
                (subject_id, day_bucket),
            ).fetchone()["n"]
            if distinct_count >= limits.distinct_mints_per_day:
                return QuotaDecision(False, REASON_DISTINCT_MINTS, _seconds_to_next_utc_day(now))

        self._increment(conn, "quota_minute", subject_id, minute_bucket)
        self._increment(conn, "quota_day", subject_id, day_bucket)
        if not mint_already_seen:
            conn.execute(
                "INSERT OR IGNORE INTO quota_mints_seen (subject_id, bucket, mint) VALUES (?, ?, ?)",
                (subject_id, day_bucket, mint),
            )
        return QuotaDecision(True)

    @staticmethod
    def _get_count(conn: sqlite3.Connection, table: str, subject_id: str, bucket: str) -> int:
        row = conn.execute(
            f"SELECT count FROM {table} WHERE subject_id = ? AND bucket = ?",
            (subject_id, bucket),
        ).fetchone()
        return row["count"] if row is not None else 0

    @staticmethod
    def _increment(conn: sqlite3.Connection, table: str, subject_id: str, bucket: str) -> None:
        conn.execute(
            f"INSERT INTO {table} (subject_id, bucket, count) VALUES (?, ?, 1) "
            f"ON CONFLICT(subject_id, bucket) DO UPDATE SET count = count + 1",
            (subject_id, bucket),
        )
