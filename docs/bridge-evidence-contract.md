# Required bridge evidence contract (for sale eligibility)

The API cannot make the bridge's data better; it can only refuse to sell
data that does not prove itself. For any token to become sellable, the AHX
read-only bridge (`GET /v1/x402/token/{mint}`, schema 1.0) must emit, in
`evidence[]`, at least one item shaped like:

```json
{
  "type": "transaction",
  "mint": "<the requested mint, exact>",
  "transaction_signature": "<base58, 64-byte Solana signature>",
  "block_time": 1760500000,
  "source": "solana-rpc"
}
```

- `mint` must equal the requested mint (association is not inferred).
- `transaction_signature` (or `tx_signature`, `signature`, or an
  `https://solscan.io|explorer.solana.com|solana.fm/tx/<sig>` URL).
- `block_time`: unix seconds from the chain (or an ISO-8601 timestamp with
  timezone in `timestamp`). The API compares it to its own clock.

And at least one observed finding: `creator.profile.verified_launches |
observed_launches | confirmed_outcomes > 0`, `wallet_activity.profile.total_events > 0`,
non-empty `wallet_activity.recent_events`, non-empty
`behavioral_findings.alerts`, or a 30+ character `behavioral_findings.explanation`.

## Gap observed on 2026-10-15

The bridge payload shape recorded in this repository's tests (the only
versioned description available; the bridge source `app/x402_bridge` is not
in GitHub) is:

```json
"evidence": [{"source": "read-only-bridge"}],
"creator": {"address": "...", "profile": {"trust_score": 100.0}},
"behavioral_findings": {"scores": {...}, "decision": "WAIT", "alerts": [], "explanation": null}
```

That shape fails every evidence rule above. If it is still what the live
bridge returns, **no investigation is sellable** under the new gate, and
under the currently deployed gate (`4df5dbe`) such a response labeled FRESH
**would be charged**. This is unverified against live data because the
bridge is not reachable from the environment that produced this report.

## Bridge-side work needed (AHX repository, not this one)

1. Commit `app/x402_bridge` and its tests to `Mthamasia/alpha-hunter-x`.
2. Emit per-evidence `mint`, `transaction_signature`, `block_time` from
   already-stored rows. Fact (schema only): the AHX `events` table
   (`models.py`, class `Event`) already has `signature`, `slot`,
   `timestamp` and `mint` columns, and `trades` has `signature`/`timestamp`.
   Whether those rows are populated for sellable mints is NOT verified.
   No new Helius call is needed to expose them.
3. Keep the bridge read-only (`?mode=ro`), loopback-only egress.
