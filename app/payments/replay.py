"""Server-side guard against reusing one x402 payment proof.

On-chain, the same signed Algorand transaction cannot be confirmed twice,
so a replayed PAYMENT-SIGNATURE cannot be charged twice: the x402 library
would get a failed settlement and swap the response for a 402. This guard
adds a cheaper and clearer layer on top:

- a proof that already settled is rejected with 409 BEFORE the route runs
  (no bridge call, no delivery, no settlement attempt);
- a proof that is currently in flight is rejected with 409, so two
  concurrent requests carrying the same proof never both reach the
  facilitator.

Only a SHA-256 of the header is stored, never the proof itself. The store
is SQLite and lives with the quota database (QUOTA_DB_PATH), so it
survives restarts when that path is persistent.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from threading import Lock

STATE_IN_FLIGHT = "in_flight"
STATE_SETTLED = "settled"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS payment_proofs (
    proof_hash TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    request_id TEXT,
    updated_at TEXT NOT NULL
);
"""


def proof_hash(header_value: str) -> str:
    return hashlib.sha256(header_value.strip().encode("utf-8")).hexdigest()


class PaymentReplayGuard:
    def __init__(self, db_path: str):
        self._db_path = db_path
        self._lock = Lock()
        self._conn: sqlite3.Connection | None = None
        if db_path == ":memory:":
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        with self._lock, self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        return sqlite3.connect(self._db_path, timeout=5)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def try_acquire(self, digest: str, request_id: str | None) -> bool:
        """Atomically claim a proof. False if it is in flight or settled."""
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO payment_proofs (proof_hash, state, request_id, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (digest, STATE_IN_FLIGHT, request_id, self._now()),
            )
            return cur.rowcount == 1

    def mark_settled(self, digest: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE payment_proofs SET state = ?, updated_at = ? WHERE proof_hash = ?",
                (STATE_SETTLED, self._now(), digest),
            )

    def release(self, digest: str) -> None:
        """Not settled (no charge happened): the payer may retry with the
        same proof, e.g. after a 503 intelligence_not_ready."""
        with self._lock, self._connect() as conn:
            conn.execute(
                "DELETE FROM payment_proofs WHERE proof_hash = ? AND state = ?",
                (digest, STATE_IN_FLIGHT),
            )

    def state(self, digest: str) -> str | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT state FROM payment_proofs WHERE proof_hash = ?", (digest,)
            ).fetchone()
            return row[0] if row else None
