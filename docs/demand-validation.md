# Demand validation plan (no claims of existing buyers)

Status 2026-10-15: zero external buyers observed. The only Mainnet payment
on record (2026-09-24) was an internal controlled test.

## Hypotheses to test, in order

| # | Hypothesis | Evidence that confirms | Evidence that refutes |
|---|---|---|---|
| H1 | Agents can find the API | ≥ 10 distinct non-internal callers of `/.well-known/x402` or `/v1/token/preview` in 14 days after listing | < 3 |
| H2 | The free preview is useful enough to come back | ≥ 30 % of preview callers return on another day | ≈ 0 returns |
| H3 | Someone pays when `premium_eligible=true` | ≥ 1 settled sale from a payer address that is not ours | 0 sales after ≥ 50 eligible previews to external callers |
| H4 | Unit economics are positive | revenue per sale (0.05) > marginal cost (bridge + Railway share; Helius 0 on the request path) | marginal cost ≥ price |

Measured only from `scripts/telemetry_report.py` (settled sales and
on-chain payers; `X-Agent-Id` is an unauthenticated label).

## Minimum actions (cheap, reversible)

1. Deploy PR #8 with persistent volumes, still at a conscious payment
   decision (see readiness report), so the free preview and telemetry exist.
2. Publish the reference agent and the integration guide in the README and
   the landing page.
3. Make the x402 Bazaar metadata accurate (Algorand settlement is stated).
4. Contact 10 builders of Solana trading agents directly with the free
   preview; ask one question: "would you pay 0.05 USDC for this response?"
5. Review H1–H4 after 14 days. If H3 has 0 sales, stop feature work and
   revisit the offer, not the code.

## Demo for an external consumer (no payment involved)

```
python examples/agent_preview_then_pay.py https://<api> <mint>
```
Shows: discovery -> free preview with eligibility and reasons -> decoded
x402 offer (network, asset, amount, payTo). The paid response example in
the docs comes from the reference bridge with synthetic rows and must be
labeled as such until a real eligible response exists.
