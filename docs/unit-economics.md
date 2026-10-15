# Unit economics per premium investigation (2026-10-15)

| Item | Value | Basis |
|---|---|---|
| Price | US$ 0.05 per settled investigation (Algorand Mainnet USDC, 50000 atomic) | FACT: `/.well-known/x402` in production |
| Helius calls per request | 0 | FACT by construction: no Helius code path in the API; bridge is read-only with no outbound sockets (audit 2026-10-12); tests assert `helius_calls_total == 0` |
| Incremental cost per request | ≈ US$ 0 measurable: one HTTPS call to the bridge + one SQLite read on an existing host | INFERENCE: fixed infrastructure, no per-call vendor fee observed |
| Fixed shared costs | Railway service + volume (500 MB), bridge host (shared with MEMNX), collector Helius plan when running | NOT VERIFIED: invoices are not accessible from this environment |
| Facilitator fee | not observed; Algorand fees are paid by the facilitator/payer | NOT VERIFIED |
| App latency, lower bound | preview p50 8.5 ms / p95 11.9 ms; paid flow (402 + pay + deliver) p50 67.7 ms / p95 108.8 ms | MEASURED in-process, simulated bridge and facilitator; excludes network, real bridge and Algorand settlement |
| Production latency, eligible rate, margin | recorded per request from PR #8 (`latency_ms`, `http_status`, `revenue_kind`) and reported by `scripts/telemetry_report.py` | Will exist only after deploy |

Margin per settled sale ≈ US$ 0.05 minus a near-zero incremental cost. The
business question is not margin per call, it is volume: covering fixed costs
needs (fixed monthly cost ÷ 0.05) settled sales per month. That volume is
unproven: zero external sales observed.

## Payment network friction (hypothesis, not acted on)

- FACT: Coinbase CDP's x402 facilitator lists Base, Polygon, Arbitrum, Monad,
  World and Solana mainnet; Algorand is not listed
  (https://docs.cdp.coinbase.com/x402/network-support, checked 2026-10-15).
- INFERENCE: an agent that consumes Solana data most likely holds USDC on
  Solana or Base, not on Algorand. Requiring Algorand USDC is probably a
  conversion barrier.
- Test before changing anything: count 402 offers served vs. payment
  attempts in telemetry after the preview is live. If agents reach the 402
  and do not pay, add Solana USDC as a second `accepts` option (x402 allows
  several) instead of replacing Algorand. Requires owner authorization.
