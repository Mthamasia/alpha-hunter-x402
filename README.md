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
`/v1/token/investigate`. **Database/cache only, by construction and by test**:
every code path under this route -- success, cache hit, unknown mint, invalid
mint, bridge auth failure, bridge 5xx, quota-blocked -- is covered by
`tests/test_preview.py::test_preview_never_calls_helius_on_any_path`, which
fails if anything on that route ever opens a socket to anything other than
the (test-doubled) bridge. STALE/unavailable/erroring data is never a pretext
for an RPC refresh.

### Quotas and API keys

Enforcement is persistent, atomic, and fail-closed (`app/quota.py`, SQLite,
`QUOTA_DB_PATH`) -- NOT the simple in-process limiter this endpoint shipped
with initially, which cannot survive a restart/redeploy or be shared across
replicas. Two tiers for this pilot (initial numbers, not final policy):

| Tier | Per minute | Per day | Distinct mints/day |
| --- | ---: | ---: | ---: |
| Anonymous (by IP) | 10 | 50 | 15 |
| `X-API-Key` (free, server-issued) | 30 | 300 | 60 |

- Each check-and-consume (minute bucket, day bucket, distinct-mint set) happens
  under one lock and one SQLite transaction, so concurrent requests cannot
  both slip past the same boundary (see
  `test_check_and_consume_is_atomic_under_thread_concurrency`).
- `429` responses carry `Retry-After` and a machine-readable `reason`
  (`per_minute_limit_exceeded` / `per_day_limit_exceeded` /
  `distinct_mints_per_day_exceeded` / `quota_store_unavailable`), computed
  *before* the cache or the bridge is touched, and are recorded in telemetry
  with `served_from="blocked"` -- never alongside successful queries.
  `quota_store_unavailable` means the quota store itself is down: the request
  is still denied (fail-closed), never silently unlimited.
- `X-Agent-Id` is a client-supplied, **unauthenticated** claim. It is recorded
  in telemetry as a label only and never resolves identity or gates quota.
  The only two identities this module recognizes are an `X-API-Key` (hashed
  with SHA-256, never stored or logged in plaintext, revocable by `key_id`)
  and the resolved client IP.
- The resolved IP is, by default, the raw transport-level peer address.
  Trusting a forwarded-for header requires BOTH `TRUSTED_PROXY_HEADER` (its
  exact name) AND `TRUSTED_PROXY_CIDRS` (the CIDR blocks the real edge/proxy
  connects from) -- configuring the header name alone is refused at startup
  (`ConfigError`), because a header *name* does not stop a client from simply
  sending it itself. The header is only honored when the immediate TCP peer's
  address falls inside `TRUSTED_PROXY_CIDRS`; otherwise it is ignored and the
  raw peer address is used, even if the header is present. **Open item**:
  Railway's actual edge/proxy IP ranges for this deployment have not been
  confirmed from this environment; do not set `TRUSTED_PROXY_CIDRS` until
  that is verified, or every anonymous caller may be bucketed under Railway's
  single edge IP instead of their own.
- The IP itself is never stored raw: before it becomes a quota `subject_id`
  it is pseudonymized (`app/quota.py:hash_identifier`) -- HMAC-SHA256 if
  `QUOTA_IP_PSEUDONYMIZATION_SALT` is configured, otherwise plain SHA-256
  (still not the IP at rest, though guessable without a salt).
- `app/ratelimit.py`'s `TTLCache`/`FixedWindowRateLimiter` remain as the
  response cache (`PREVIEW_CACHE_TTL_SECONDS`) -- not as quota enforcement --
  and are bounded (`max_entries`, LRU eviction) so a scan of many distinct
  mints/IPs cannot grow them without limit.
- **Retention**: quota buckets keep no history beyond the current minute/day
  -- a past bucket has zero future enforcement value, which is also the
  simplest correct privacy posture. Telemetry rows are kept
  `TELEMETRY_RETENTION_DAYS` (default 90). Both are actually purged, not just
  purgeable: `app/retention.py` runs an `asyncio` loop from the FastAPI
  lifespan every `RETENTION_PURGE_INTERVAL_SECONDS` (default 3600) calling
  `QuotaStore.purge_expired()`/`CostLedger.purge_older_than()`. The retention
  *period* itself is an operational default, not a compliance sign-off --
  confirm it against your actual obligations before relying on it.

## Cost and revenue telemetry

`app/telemetry.py` records, for every `preview` and `investigate` request, a
row in a dedicated SQLite store (`TELEMETRY_DB_PATH`, default `:memory:` =
not persisted until an operator opts in) -- always separate from the Alpha
Hunter intelligence database, which this service never opens directly.

Each row carries: `request_id`, route, mint, an unauthenticated `X-Agent-Id`
label, `served_from` (`cache` / `bridge` / `stub` / `blocked`), Helius
call/retry counts, credits confirmed vs. estimated vs. unknown, revenue state,
and an estimated cost/margin. **Nothing here makes a Helius RPC call.** Both
current data paths (the preview cache and the read-only bridge) are
SQLite-backed, so every request today is correctly recorded as zero Helius
calls and zero cost -- the instrumentation is forward-looking, built and
tested so a future selective on-demand update feature can wrap its real RPC
calls in the same `HeliusCallTracker`/`BudgetGuard` with no new plumbing.

Two things this stage's review specifically fixed:

- **Cost is never silently zero.** A call whose credits are neither confirmed
  nor estimated is *unknown*, never priced at zero. Just as importantly: a
  call with known, positive credits but **no configured** `$/credit` rate
  (`HELIUS_CREDIT_COST_USD` unset, i.e. `None`) is *also* unknown cost, never
  `0` -- a positive credit count must not look free just because nobody
  configured the rate. Only zero Helius calls, or an explicitly configured
  rate (including an explicit `0`), produce a real `0`/priced cost. Margin is
  `None` whenever cost is `None`.
- **Revenue is recorded only after settlement is confirmed.** `investigate`
  writes a `pending` row *before* the x402 facilitator settles (the route
  cannot know the outcome yet -- settlement happens in the x402 middleware,
  after the route returns). The middleware finalizes that exact row to
  `settled` (real revenue) or `failed` (no revenue) once the facilitator's
  actual result is known; a `pending` row is never read as revenue by the
  aggregates. `disabled` mode records `none`; `test` mode (synchronous,
  simulated, no real facilitator) records `simulated` immediately, same as
  before.
- **A telemetry failure can never flip a paid response into an error.** The
  finalization step above is isolated in its own `try`/`except`: if a real
  settlement already succeeded and only the SQLite write afterwards fails,
  the client still gets their 200. The failure is logged at `CRITICAL`
  (`UNRECONCILED SETTLEMENT`) for manual follow-up instead of silently
  swallowed or, worse, turned into a 503 for a customer who already paid.

`BudgetGuard` is SQLite-backed (`ledger_db_path`) so its daily spend survives
a restart, and its reservation is atomic: the check (per-query limit, daily
budget -- including credits other in-flight calls have already reserved, not
just confirmed spend -- and concurrency) and the reservation itself happen
under the same lock and the same transaction. `reserve()` yields a handle
with three, deliberately asymmetric, outcomes:

- `handle.commit(actual_credits)` -- the real cost is known; charge exactly that.
- `handle.cancel()` -- the call definitely never reached Helius (e.g. input
  validation failed before any request was sent); a real, confirmed zero cost.
- Neither called (including when the `with` block exits via an exception,
  e.g. an RPC timeout) -- **ambiguous, not free**: the full reservation is
  charged. A timeout does not prove Helius never billed the attempt, so the
  tracked budget must not understate real usage just because the outcome
  is unconfirmed.

Unknown credit estimates are refused by default (`allow_unknown_credits=False`)
-- an "unknown-cost" call must not be able to bypass every cap for free. All
caps (`HELIUS_DAILY_BUDGET_CREDITS`, `HELIUS_PER_QUERY_CREDIT_LIMIT`) are
unset by default; nothing is enforced until an operator configures them, and
**nothing in this repository calls Helius yet either way.**

This repository's own code never calls Helius (verified by the tests above);
it talks to the AHX bridge over HTTPS, and the bridge itself only ever reads
SQLite. That second half of the claim is verified separately, read-only,
against the live bridge -- see
[docs/vps-bridge-read-only-audit.md](docs/vps-bridge-read-only-audit.md).

`CostLedger.totals_by_mint/agent/period` answer "how much did this token /
agent / period cost and earn" directly from the stored rows; a total is never
summed as complete if any underlying row's cost is unknown.
`CostLedger.purge_older_than(cutoff_iso)` implements the retention mechanism
(delete rows before a cutoff) -- the actual retention *policy* (how long, and
sign-off that it satisfies applicable data-protection obligations for
whatever personal data `X-Agent-Id`/IP-derived subject IDs constitute in this
deployment) is a product/legal decision this PR does not make for you.
Reconciling Helius estimates against the real Helius dashboard is a manual
step for now, and must account for the same Helius account/key potentially
serving other processes (the VPS collector, other AHX tooling) -- this ledger
only ever knows about requests that passed through this API, and overall
margin claims must not include infrastructure costs (VPS, Railway) that this
ledger does not track.

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
| `PREVIEW_CACHE_TTL_SECONDS` | Free preview per-mint response cache TTL, seconds. Default `60`. |
| `QUOTA_DB_PATH` | Persistent quota/API-key store. Default `:memory:` (not persisted -- set a real path in production). |
| `TRUSTED_PROXY_HEADER` | Header name to trust for client IP (e.g. `X-Forwarded-For`). Unset = trust nothing but the raw peer address. |
| `QUOTA_ANONYMOUS_PER_MINUTE` / `_PER_DAY` / `_DISTINCT_MINTS_PER_DAY` | Anonymous (IP-based) preview quota. Defaults `10` / `50` / `15`. |
| `QUOTA_REGISTERED_PER_MINUTE` / `_PER_DAY` / `_DISTINCT_MINTS_PER_DAY` | `X-API-Key` preview quota. Defaults `30` / `300` / `60`. |
| `HELIUS_DAILY_BUDGET_CREDITS` | Optional daily credit cap for `BudgetGuard`. Unset = no cap. |
| `HELIUS_PER_QUERY_CREDIT_LIMIT` | Optional per-query credit cap for `BudgetGuard`. Unset = no cap. |
| `HELIUS_CREDIT_COST_USD` | USD cost per Helius credit. **Unset by default** -- a positive credit count with no configured rate prices as unknown, never `0`. |
| `TELEMETRY_DB_PATH` | Cost/revenue ledger SQLite path. Default `:memory:` (not persisted -- set a real path in production). |

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

## Commercial readiness (2026-10-15)

- [Agent integration guide](docs/agent-integration.md): free preview, then pay only if `premium_eligible`.
- [Required bridge evidence contract](docs/bridge-evidence-contract.md)
- [Readiness report and GO/NO-GO verdict](docs/commercial-readiness-report-2026-10-15.md)
- Operator metrics: `python scripts/telemetry_report.py $TELEMETRY_DB_PATH` (read-only).
