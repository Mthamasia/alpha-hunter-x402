# AHX x402 — first-paying-customer experiment (2026-10-12)

**Objective:** secure one independent, verifiable payment from a real developer customer for useful behavioral Solana intelligence, without trading the owner's funds or consuming additional Helius quota while collection is suspended.

## Evidence and current blockers

| Dimension | What is known | Status / action |
| --- | --- | --- |
| Product | `POST /v1/token/investigate`, x402 V2 exact payment, quality metadata | Implemented in repository |
| Mainnet payment | Single authorized 0.05 USDC Mainnet E2E on 2026-09-24 | Test by owner, NOT evidence of a customer |
| Data freshness | Mainnet E2E response `STALE` | **Sales-blocking** until independently audited |
| Public uptime | Prior deployment documented on Railway | Unverified on 2026-10-12 |
| Payment network | Algorand Mainnet USDC | Friction for Solana/Base-native agent buyers |
| Discoverability | Bazaar extension attached to offer | Indexing and buyer demand NOT verified |
| Marketing | No evidence of independent paid customers | Prospect outreach remains unexecuted |
| Helius | Owner has suspended new Helius-consuming collection | Do not restart or provision more without approval |

## Positioning — a complementary B2B signal, not another rug score

**Buyer:** software team running Solana trading agents that already obtains contract safety and routing checks.

**Problem hypothesis:** safety checklists often miss historical creator/wallet behaviors and do not tell an automated decision-maker which conclusion rests on which evidence. Test whether buyers will integrate point-in-time behavioral findings with explicit data-quality flags rather than another black-box safety score.

**Offer to test:** one `/v1/token/investigate` request for 0.05 USDC on Algorand Mainnet, plus a human-reviewed test result and a reproducible integration sample. This is a **proposed pilot offer**, not a sale, guarantee, or production SLA. If freshness is unacceptable, do not offer a paid evaluation.

**Do not promise:** guaranteed rug avoidance, 60 snipes/day, a particular profit, access to real-time data or instant response latency.

## Pre-sale acceptance gate (must be evidenced)

1. Confirm public API runtime and x402 challenge without signing or paying. Document UTC timestamp and HTTP statuses.
2. With existing read-only data only, pre-register a representative set of 25 mints (established token, recent token, prior AHX-observed token, unknown mint). Record response quality **before looking at results**.
3. Produce a report with row-level mint, observation timestamp, creator_verified evidence, `FRESH/STALE/NO_DATA`, field coverage, HTTP status and latency. Do not count blank objects as useful findings.
4. Establish baseline server/RPC cost per successful useful response and daily permitted Helius budget; do not assume zero marginal cost.
5. Define go/no-go: at least 20/25 requests return technically valid responses; at least 15/25 have enough attributable evidence for an independent reviewer to make a decision; no unverified creator or stale datum may be labeled current. **These are pilot targets, not achieved numbers.**
6. Confirm Algorand payer flow with a consented customer or replace payment friction with a separately approved implementation. No automated live financial signing.

If the quality gate fails, revise the product to a **historical forensics/evidence API** where snapshots are explicitly dated, not real-time execution intelligence.

## Prospective integrators (verified existence only, NOT interested leads)

These are target accounts for **permission-based 1:1 research**, not people who have agreed to buy.

| Target | Why the integration hypothesis is relevant | Public source |
| --- | --- | --- |
| Parasol | Solana autonomous trading with manipulation checks | https://parasol.so/whitepaper |
| Condor Agents | Agent creation and on-chain tools, including safety-sensitive LP operations | https://www.condor.so/ |
| Avo | Solana marketplace for trading agents | https://www.avo.so/ |
| PumpMyClaw | Developer-focused agent trading infrastructure | https://ankush.one/projects/2026.pumpmyclaw/ |
| Wenrwa | Solana trading SDK and agent tooling | https://www.wenrwa.com/ |
| agentcomponents/solana-trading-bot | Open-source bot with existing RugCheck/GoPlus safety layer | https://github.com/agentcomponents/solana-trading-bot |

Do not claim these teams need AHX or have budget until direct evidence is obtained. Avoid spam and never open issues merely to advertise a product.

## Three independent workstreams — four-seat desk

- **HEAD (sales coordinator):** tracks gate status, prospect ID, owner approval and an immutable evidence ledger; no claims unsupported by evidence.
- **DATA QA:** validates only local/read-only evidence, finds stale coverage and missing provenance; never restarts Helius.
- **INTEGRATION:** maintains a no-wallet quote walkthrough and one runnable example; never requests private keys, never signs.
- **COMMERCIAL:** qualifies buyer fit, prepares six individualized 1:1 drafts, runs a permission-based pilot once authorized; never sends outreach, publishes ads or spends funds without owner approval.

All work must carry `lead_id`, `source_url`, `evidence_at`, `quality_status`, `next_action`, and `approved_by` where applicable. Silence is not evidence of uptime.

## First-contact draft (NOT sent)

Subject: Behavioral evidence for Solana trading agents — integration question

Hello — I'm testing a small paid API that returns Solana token creator/wallet behavioral findings with explicit evidence and data-quality flags. It is intended to complement your existing contract/liquidity checks, not execute trades.

Would a dated, explainable behavioral signal be useful in your pre-trade screening workflow? If so, which fields and freshness threshold would make a low-cost paid API worth integrating?

I can share the response schema and the limitations before any payment. No keys or wallet access are needed for that review.

## Commercial decision rule

Track: `relevant_accounts_researched`, `personalized_outreach_approved`, `sent`, `replied`, `integration_tests_requested`, `independent_paid_receipts`, `gross_receipts`, `provider_cost`, `net_contribution`.

After ten genuinely qualified 1:1 outreach attempts and three independent technical evaluations, either:
- pursue a paying segment based on a signed receipt and proof of repeat usage; or
- stop the segment and document why (freshness, price/network, low need, or integration cost).

**No current revenue is established in this document.** The test payment from the owner is not customer revenue. The evidence is not sufficient to advertise any profit claims.
