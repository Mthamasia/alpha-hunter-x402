# Alpha Hunter X x402 API

Paid behavioral on-chain intelligence for autonomous agents. Submit a Solana
token mint and receive structured creator, wallet activity, behavioral findings,
evidence, concentration, and explicit data-quality metadata.

The public satellite never opens the Alpha Hunter database. It uses an
authenticated, read-only HTTP bridge through `AlphaHunterProvider`.

## Why x402

The API uses HTTP 402 and x402 V2 `exact` payments. A client first receives a
`PAYMENT-REQUIRED` header, validates the requirement, signs with its own wallet,
and retries once with `PAYMENT-SIGNATURE`. The server has no wallet, private key,
or seed phrase; settlement is performed by the configured facilitator.

```text
Agent
  -> AHX x402 API
  <- HTTP 402 / PAYMENT-REQUIRED
Agent wallet -> Algorand payment signature
  -> GoPlausible facilitator verify + settlement
  -> AlphaHunterProvider -> read-only bridge -> Alpha Hunter intelligence
  <- HTTP 200 / PAYMENT-RESPONSE
```

## Paid endpoint

`POST /v1/token/investigate`

```json
{"chain":"solana","mint":"So11111111111111111111111111111111111111112"}
```

The input is strict: `chain` must be `solana`, `mint` must be a 32-byte Base58
Solana address, and additional fields are rejected.

The response is versioned (`schema_version: "1.0"`) and includes:

```json
{
  "status": "STALE",
  "intelligence": {
    "creator": {},
    "wallet_activity": {},
    "concentration": null,
    "behavioral_findings": {},
    "evidence": []
  },
  "data_quality": {
    "status": "STALE",
    "limitations": ["Data freshness limitations are returned verbatim."]
  }
}
```

`FRESH`, `STALE`, `NO_DATA`, and other provider data-quality states are never
upgraded or inferred by the satellite. A bridge 404 returns `NO_DATA` with empty
intelligence; the API never fabricates evidence.

## Free preview endpoint

`POST /v1/token/preview` is free and requires no payment. It calls the exact
same `AlphaHunterProvider` path as the paid endpoint -- so a successful
response is a real proof that this API reaches the read-only bridge and the
Alpha Hunter database, not a canned reply -- but returns only availability,
freshness, and data-quality:

```json
{
  "schema_version": "1.0",
  "mint": "So11111111111111111111111111111111111111112",
  "available": true,
  "status": "STALE",
  "age_seconds": 2745044,
  "is_stale": true,
  "data_quality": {"status": "STALE", "limitations": ["..."]}
}
```

It never returns `intelligence` (creator, wallet activity, behavioral
findings, evidence, concentration) -- that remains exclusive to the paid
`/v1/token/investigate`. Two protections exist specifically for this free,
unauthenticated endpoint (the general rate-limiting policy above still
applies, and is still better enforced at the edge for multi-instance
deployments):

- An in-memory, per-mint TTL cache (`PREVIEW_CACHE_TTL_SECONDS`, default 60s)
  so repeated agent queries for the same token in a short window cost one
  bridge round trip, not N.
- An in-memory, per-client-IP fixed-window rate limit
  (`PREVIEW_RATE_LIMIT_PER_MINUTE`, default 30/min) returning `429`.

Both are per-process state (see Dockerfile: a single uvicorn worker). They do
not survive a restart and are not shared across replicas; that is an accepted
limitation for this stage, not a hidden one.

## Cost and revenue telemetry

`app/telemetry.py` records, for every `preview` and `investigate` request, a
row in a dedicated SQLite store (`TELEMETRY_DB_PATH`, default `:memory:` =
not persisted until an operator opts in) -- always separate from the Alpha
Hunter intelligence database, which this service never opens directly.

Each row carries: `request_id`, route, mint, optional `X-Agent-Id`,
`served_from` (`cache` / `bridge` / `stub`), Helius call/retry counts,
credits confirmed vs. estimated vs. unknown, revenue (`real` / `simulated` /
`none`), and an estimated cost/margin. **Nothing here makes a Helius RPC
call.** Both current data paths (the preview cache and the read-only bridge)
are SQLite-backed, so every request today is correctly recorded as zero
Helius calls and zero cost -- the instrumentation is forward-looking, built
and tested so a future selective on-demand update feature can wrap its real
RPC calls in the same `HeliusCallTracker`/`BudgetGuard` with no new plumbing.
A call whose credits are neither confirmed nor estimated is recorded as
*unknown* and is never priced at zero -- cost (and therefore margin) becomes
unknown too, rather than invented.

`BudgetGuard` prepares a daily global credit budget, a per-query credit cap,
and a concurrency cap (`HELIUS_DAILY_BUDGET_CREDITS`,
`HELIUS_PER_QUERY_CREDIT_LIMIT`); all unset by default (no cap enforced yet).
`CostLedger.totals_by_mint/agent/period` answer "how much did this token /
agent / period cost and earn" directly from the stored rows. Reconciling
these estimates against the Helius dashboard is a manual step for now, and
must account for the same Helius account/key potentially serving other
processes (the VPS collector, other AHX tooling) -- this ledger only knows
about requests that passed through this API.

## Bazaar discovery and Global x402 Challenge

The x402 `PAYMENT-REQUIRED` response declares the Bazaar extension using the
current x402 schema. It describes the HTTP method, request example, JSON output,
pricing requirements, and includes the `x402-global-challenge` tag. Facilitators
that support Bazaar can catalog the public resource after a successful public
settlement.

The repository declares discovery metadata in the payment requirements.
Bazaar indexing depends on the facilitator and has not been independently
verified for the current Algorand Mainnet deployment.

## Buyer materials

- [No-wallet buyer quickstart and limitations](docs/buyer-quickstart.md)
- [Evidence-first first-customer experiment](docs/first-customer-pilot.md)

## Testnet proof

A paid Algorand Testnet E2E completed successfully through Pera Wallet, x402,
GoPlausible, the satellite, `AlphaHunterProvider`, and the read-only bridge.
The public, non-secret evidence is recorded in
[docs/e2e-testnet-proof.md](docs/e2e-testnet-proof.md).

## Configuration

Copy `.env.example` to a local `.env`; it is ignored by Git. Never put wallet
keys, mnemonics, or bridge tokens in versioned files.

| Variable | Purpose |
| --- | --- |
| `PAYMENT_MODE` | `disabled`, `test`, `x402-testnet`, or explicit `x402-mainnet`. |
| `PRICE_USD` | Decimal price; `0.05` is `50000` USDC atomic units. |
| `PAY_TO` | Required Algorand receiver in real x402 Testnet and Mainnet modes. |
| `X402_NETWORK` | Algorand Testnet or Mainnet alias/CAIP-2 matching `PAYMENT_MODE`. |
| `X402_FACILITATOR_URL` | HTTPS x402 facilitator URL without credentials. |
| `INTELLIGENCE_PROVIDER` | `stub` or `alpha_hunter`. No silent fallback occurs. |
| `AHX_BRIDGE_URL` | Read-only bridge URL. |
| `AHX_BRIDGE_SERVICE_TOKEN` | Required only for `alpha_hunter`; never log it. |
| `PUBLIC_BASE_URL` | Required HTTPS public API origin in production. |
| `CORS_ALLOW_ORIGINS` | Required comma-separated HTTPS browser origins in production. |
| `ENABLE_MAINNET_X402` | Must be exactly `true` before `x402-mainnet` is accepted. |
| `PREVIEW_CACHE_TTL_SECONDS` | Free preview per-mint cache TTL, seconds. Default `60`. |
| `PREVIEW_RATE_LIMIT_PER_MINUTE` | Free preview per-IP rate limit. Default `30`. |
| `HELIUS_DAILY_BUDGET_CREDITS` | Optional daily credit cap for `BudgetGuard`. Unset = no cap. |
| `HELIUS_PER_QUERY_CREDIT_LIMIT` | Optional per-query credit cap for `BudgetGuard`. Unset = no cap. |
| `HELIUS_CREDIT_COST_USD` | USD cost per Helius credit, for cost estimation. Default `0`. |
| `TELEMETRY_DB_PATH` | Cost/revenue ledger SQLite path. Default `:memory:` (not persisted). |

`APP_ENV=production` fails closed unless `PUBLIC_BASE_URL` and
`CORS_ALLOW_ORIGINS` are configured. It rejects a localhost bridge and requires
HTTPS for the Alpha Hunter bridge.

## Security model

- Request bodies are limited and Pydantic input is strict.
- Responses carry request IDs, `nosniff`, frame denial, restrictive permissions,
  no-referrer, and `no-store` headers.
- CORS is disabled unless explicit origins are configured; production forbids
  wildcard-style configuration.
- Provider timeout, connection, authentication, schema, and mint mismatches fail
  closed. A provider failure never becomes a valid empty investigation.
- The bridge service token is sent only as `X-AHX-Bridge-Token` to the configured
  bridge. No direct SQLite, Alpha Hunter module, or Helius access exists here.
- Rate limiting belongs at the HTTPS edge/reverse proxy. In-process limiting is
  intentionally not presented as multi-instance protection.

## Local development

```powershell
.venv\Scripts\python.exe -m pytest
cd integration-client
npm.cmd test
npm.cmd run build
```

Run the API with a safe local mode:

```powershell
$env:PAYMENT_MODE="disabled"
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Useful unauthenticated operational endpoints are `GET /health`, `GET /ready`,
and `GET /version`; none returns bridge credentials or intelligence.

## Container and production deployment

Build only the satellite:

```bash
docker build -t ahx-x402-api .
docker run --env-file .env -p 8000:8000 ahx-x402-api
```

Recommended architecture: expose this container only behind an HTTPS reverse
proxy/WAF that terminates TLS, enforces request-size and distributed rate limits,
and forwards only the API origin. Keep the Alpha Hunter bridge on a private
network reachable from the satellite over authenticated HTTPS. Do not expose the
bridge or the Alpha Hunter database to the internet.

## Mainnet status

The server supports the official `x402-avm==2.0.2` Mainnet AVM profile with
`ALGORAND_MAINNET_CAIP2` and `USDC_MAINNET_ASA_ID` (`31566704`). It is never a
default or fallback: activating `x402-mainnet` requires all of:

- `APP_ENV=production`;
- `ENABLE_MAINNET_X402=true`;
- `INTELLIGENCE_PROVIDER=alpha_hunter`;
- a non-localhost HTTPS Alpha Hunter bridge plus its service token;
- explicit HTTPS public API URL and CORS origins; and
- an HTTPS facilitator.

The repository includes an isolated Algorand Mainnet browser client in
[`integration-client-mainnet/`](integration-client-mainnet/README.md).
A single authorized paid Mainnet end-to-end test completed on 2026-09-24;
see [the Mainnet evidence record](docs/public-mainnet-e2e-proof.md).
The test response was **STALE**, not evidence of fresh data, customer demand,
or ongoing public uptime. No independent customer payment is documented.

## Disclaimer

Alpha Hunter X intelligence is informational on-chain analysis, not investment,
trading, legal, or financial advice. Data may be stale, incomplete, unavailable,
or inaccurate. The service makes no promise of profit or outcome.
