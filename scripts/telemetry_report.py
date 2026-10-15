"""Read-only operator report over the x402 API telemetry ledger.

Usage:  python scripts/telemetry_report.py /path/to/telemetry.db [--since 2026-10-01]

Opens the SQLite file with mode=ro (never writes). Answers the commercial
questions that matter before claiming demand: who called, which route,
what was delivered, what was actually SETTLED (not merely attempted),
what failed, and whether any Helius credit was spent.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from decimal import Decimal


def report(db_path: str, since: str | None = None) -> dict:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    where, params = ("WHERE timestamp >= ?", (since,)) if since else ("", ())
    rows = conn.execute(f"SELECT * FROM query_costs {where}", params).fetchall()
    conn.close()

    def count(key):
        out: dict[str, int] = {}
        for r in rows:
            out[str(r[key])] = out.get(str(r[key]), 0) + 1
        return dict(sorted(out.items()))

    keys = rows[0].keys() if rows else []
    settled = [r for r in rows if r["revenue_kind"] == "settled"]
    agents = {r["agent_id"] for r in rows if r["agent_id"]}
    payers = {r["payer"] for r in settled if "payer" in keys and r["payer"]}
    paying_agents = {r["agent_id"] for r in settled if r["agent_id"]}
    def pct(values, q):
        values = sorted(v for v in values if v is not None)
        if not values:
            return None
        return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]

    latency = {}
    for route in sorted({r["route"] for r in rows}):
        lat = [r["latency_ms"] for r in rows if r["route"] == route and "latency_ms" in keys]
        latency[route] = {"p50_ms": pct(lat, 0.50), "p95_ms": pct(lat, 0.95), "n": len([x for x in lat if x is not None])}
    inv = [r for r in rows if r["route"] == "investigate" and r["served_from"] in ("bridge", "stub")]
    statuses = [r["http_status"] for r in inv if "http_status" in keys and r["http_status"] is not None]
    eligible = sum(1 for s_ in statuses if s_ == 200)
    cost = [Decimal(r["cost_usd_estimated"]) for r in settled if r["cost_usd_estimated"] is not None]
    revenue = sum((Decimal(r["revenue_usd"]) for r in settled), Decimal("0"))
    return {
        "since": since,
        "latency_by_route": latency,
        "investigate_answered": len(statuses),
        "investigate_eligible_rate": (round(eligible / len(statuses), 4) if statuses else None),
        "incremental_cost_usd_settled": str(sum(cost, Decimal("0"))) if len(cost) == len(settled) else None,
        "incremental_margin_usd_settled": str(revenue - sum(cost, Decimal("0"))) if len(cost) == len(settled) else None,
        "queries": len(rows),
        "by_route": count("route"),
        "by_served_from": count("served_from"),
        "by_revenue_kind": count("revenue_kind"),
        "distinct_declared_agents": len(agents),
        "distinct_paying_declared_agents": len(paying_agents),
        "distinct_onchain_payers": len(payers),
        "settled_sales": len(settled),
        "settled_revenue_usd": str(sum((Decimal(r["revenue_usd"]) for r in settled), Decimal("0"))),
        "helius_calls_total": sum(r["helius_call_count"] for r in rows),
        "note": "agent_id is an unauthenticated X-Agent-Id label; on-chain payers and settled sales are the only proof of paying demand.",
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("db_path")
    ap.add_argument("--since")
    args = ap.parse_args()
    print(json.dumps(report(args.db_path, args.since), indent=2))
