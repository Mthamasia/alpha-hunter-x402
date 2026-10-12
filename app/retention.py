"""Periodic retention purge: runs `QuotaStore.purge_expired()` and
`CostLedger.purge_older_than()` on a timer for as long as the app
process is alive.

This exists because having a `purge_*` method is not a retention
mechanism by itself -- something has to actually call it, repeatedly,
in production. It is intentionally a plain `asyncio` loop started from
the FastAPI lifespan rather than a separate scheduler/queue service: no
new infrastructure, consistent with everything else in this stage being
SQLite-backed and single-process.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.quota import QuotaStore
from app.telemetry import CostLedger

logger = logging.getLogger("ahx.retention")


async def periodic_purge_loop(
    quota_store: QuotaStore,
    cost_ledger: CostLedger,
    *,
    interval_seconds: float,
    telemetry_retention_days: int,
) -> None:
    while True:
        try:
            quota_removed = quota_store.purge_expired()
            cutoff = (
                datetime.now(timezone.utc) - timedelta(days=telemetry_retention_days)
            ).isoformat()
            telemetry_removed = cost_ledger.purge_older_than(cutoff)
            if any(quota_removed.values()) or telemetry_removed:
                logger.info(
                    "retention purge: quota=%s telemetry_rows=%d", quota_removed, telemetry_removed
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("retention purge iteration failed; will retry next interval")
        await asyncio.sleep(interval_seconds)
