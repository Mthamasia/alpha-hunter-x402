# Alpha Hunter X — buyer quickstart (read-only, no wallet keys)

> Status: a single public Algorand Mainnet x402 payment was recorded on 2026-09-24. Availability, freshness and customer readiness must be rechecked before commercial sale. This is NOT a claim of an active SLA.

## What an AI trading desk can buy

`POST /v1/token/investigate` — behavioral Solana token intelligence (creator, wallet activity, concentration, behavioral findings and per-finding evidence). Every response carries `status` and `data_quality`. Stale, missing and unavailable data are not represented as fresh or safe.

The offering is **behavioral due diligence**, NOT automatic trade execution, rug immunity, investment advice, or guaranteed profitability. It supplements (rather than replaces) authority, liquidity and execution checks such as RugCheck or Jupiter.

- Intended buyer: developer of a trading bot / wallet intelligence agent.
- Implemented default price: 0.05 USDC per call, settled on **Algorand Mainnet**, NOT Solana or Base.
- Mainnet evidence: [recorded public paid E2E](public-mainnet-e2e-proof.md). Its response status was **STALE**.
- Public URL recorded in deployment documents: `https://alpha-hunter-x402-production.up.railway.app`. Its current reachability and runtime configuration have NOT been verified by this document.
- Live payment requires an Algorand Mainnet USDC wallet (the repository includes `integration-client-mainnet/`).
- An Alpha Hunter data-quality gate must pass before paid prospect outreach; see [first-customer pilot](first-customer-pilot.md).

## Inspect the payment offer without paying

The following request **does not contain a payment signature**, so an x402-enabled server should return a `402 Payment Required` challenge. It does not call the protected intelligence provider and does not sign or transfer funds.

```bash
curl -i --max-time 10 -X POST \
  'https://alpha-hunter-x402-production.up.railway.app/v1/token/investigate' \
  -H 'Content-Type: application/json' \
  --data '{"chain":"solana","mint":"So11111111111111111111111111111111111111112"}'
```

Inspect `PAYMENT-REQUIRED`: it should specify the expected method, resource, `exact` scheme, network, asset, amount and recipient. HTTP `402` only proves a challenge is being offered; it does NOT prove the backend has fresh token intelligence or that the facilitator will settle. Do not copy seed phrases/private keys into a message, document or bot-controlled page.

**Required commercial smoke checks (not asserted as passed today):**

1. GET `/health`, `/ready`, `/version`: verify public reachability and expected provider.
2. Unsigned POST above: verify challenge and its Algorand Mainnet details **without paying**.
3. Read-only freshness audit against real stored datasets: determine % `FRESH`, % `STALE`, % `NO_DATA`, age distribution and incomplete field rates for a predetermined 25-mint sample.
4. Provider failure, bridge failure and timeout tests must fail closed; no degraded data can be labeled fresh.
5. Only after freshness, operating cost and permission checks, request a separately approved **single** live purchase via human wallet confirmation. No unattended real-money payments.

## Output contract

The JSON body is schema versioned (currently 1.0). An example of the status and quality fields is:

```json
{
  "schema_version": "1.0",
  "status": "STALE",
  "data_quality": {
    "status": "STALE",
    "limitations": ["Data freshness limitations are returned verbatim."]
  }
}
```

This is an abbreviated illustration, **not** a complete live investigation response. See `app/models.py` and `/openapi.json` for the full schema.

## Integration boundaries

1. SEARCH produces a Solana mint with its source and an immutable lead ID.
2. RISK invokes the paid investigate endpoint only when a valid, approved budget exists.
3. RISK cross-checks the returned mint, schema version, timestamps and quality flags; if stale or unavailable it returns `INSUFFICIENT_EVIDENCE`.
4. Other independent sources perform contract and execution checks. No buying is inferred from one API's score.
5. Any live transaction signature, transfer or raised risk limit requires explicit human authorization outside the agent.

**Distribution limitation:** Bazaar metadata in a payment challenge does not guarantee a resource is indexed by any particular facilitator. Current support for Algorand in third-party discovery directories must be verified independently.