# Machine-to-machine integration guide (agents)

Status: describes the code on branch `integration/commercial-readiness`.
Production currently runs `4df5dbe`, which does **not** have
`/v1/token/preview` or `premium_eligible` yet. Do not point buyers here
until that branch is deployed.

## The intended agent loop

```
1. GET  /.well-known/x402            -> discover price, routes, payment protocol
2. POST /v1/token/preview  (free)    -> premium_eligible? reason codes?
3. if premium_eligible:
       POST /v1/token/investigate     -> 402 + PAYMENT-REQUIRED (no charge)
       sign x402 payment (Algorand Mainnet USDC, 0.05)
       POST /v1/token/investigate + PAYMENT-SIGNATURE
         200 -> intelligence, settled once (PAYMENT-RESPONSE header)
         503 intelligence_not_ready -> NOT charged, reasons[] explains why
   else:
       do not pay; treat token as INSUFFICIENT_EVIDENCE
```

Step 2 uses the same gate as settlement, so an agent that follows it does
not have to start a payment flow for a token that cannot be sold. There is
still a small race (data can age between preview and payment); in that
case step 3 returns 503 and the payment is not settled.

## Free preview

```bash
curl -s -X POST "$BASE/v1/token/preview" \
  -H 'Content-Type: application/json' -H 'X-Agent-Id: my-bot/1.0' \
  --data '{"chain":"solana","mint":"<MINT>"}'
```

Example shape (illustrative values):

```json
{
  "schema_version": "1.0",
  "chain": "solana",
  "mint": "<MINT>",
  "available": true,
  "status": "STALE",
  "age_seconds": 2745044,
  "is_stale": true,
  "data_quality": {"status": "STALE", "limitations": ["..."]},
  "premium_eligible": false,
  "premium_ineligibility_reasons": ["data_not_fresh", "no_verifiable_transaction_evidence"]
}
```

Limits (defaults, configurable): anonymous 10/min, 50/day, 15 distinct
mints/day per IP; `X-API-Key` holders 30/min, 300/day, 60 distinct
mints/day. Over limit: HTTP 429 with `Retry-After`. Repeated calls for the
same mint within 60 s are served from cache and never reach the bridge.

## Reason codes (stable)

| code | meaning |
|---|---|
| `data_not_fresh` | the bridge does not classify the data as FRESH (or it is inconsistent) |
| `no_verifiable_transaction_evidence` | no evidence item has a valid Solana tx signature / explorer URL |
| `evidence_not_linked_to_requested_mint` | evidence exists but is not explicitly about this mint |
| `evidence_without_onchain_timestamp` | evidence has no usable on-chain time |
| `evidence_older_than_freshness_window` | newest linked tx is older than the window (default 24 h) by our clock |
| `evidence_timestamp_in_future` | timestamps are implausible |
| `no_observed_behavioral_finding` | only scores/labels, no observed outcome, event or explained alert |

## Paid response guarantees

When a 200 is returned under x402 Mainnet:
- at least one evidence item has a valid Solana transaction signature,
  `mint` equal to the requested mint and a recent `block_time`;
- at least one observed finding exists (creator launch outcomes, wallet
  events, alerts, or an explanation of 30+ characters);
- creator reputation without observed outcomes is `"UNKNOWN"`;
- anomalous or contradictory market caps are removed and listed in
  `data_quality.limitations`;
- internal fields (tokens, paths, DB names, private keys) are stripped.

Verify independently: look up each `transaction_signature` on any Solana
explorer and check it touches the mint.

## Payment safety

- Settlement happens only after the route finished and returned < 400
  (x402-avm 2.0.2 middleware). A 503 `intelligence_not_ready` is never
  settled; tests assert the facilitator's `settle()` was not called.
- If settlement fails, the library replaces the body with a 402; the
  intelligence is not delivered.
- One payment proof buys one delivery: reusing a settled proof returns
  `409 payment_proof_already_used` before any work is done. A proof that
  was not settled (e.g. 503) can be retried.
- Paid calls are attributed to the on-chain payer address and settlement
  transaction from the facilitator, not to the `X-Agent-Id` label.

## Reference agent

`examples/agent_preview_then_pay.py` (standard library only) runs
discovery, free preview, decision, and decodes the 402 offer. It never
signs or pays.

## What this API is not

Not trade execution, not investment advice, not a rug guarantee. Treat it
as one behavioral input among independent checks.
