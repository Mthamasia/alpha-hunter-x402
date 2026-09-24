# x402 Testnet E2E Proof

- Test completed: 2026-09-24 15:01 -03:00
- Base commit: `36b7432`
- Result: successful paid end-to-end investigation

## Payment evidence

- Network: Algorand Testnet
- USDC Testnet ASA: `10458941`
- Amount: `50000` atomic units (`0.05` USDC)
- Receiver: `A5D55SSWZFKTMSZ2CCCOMDVM3J4ROZT2Z2KAKXM26WQEVSLVSM3ZEEPKMQ`
- Public payer: `3KSUELDHDB73AVCRNQSJD27HVCAIET4LT34FPM6L25IQ34IMZMJM2D4V7E`
- Transaction ID: `WCQCINNGJOOQREMR4O2TUNERIANOZHPSRAQL63DAF6HLCHWTRE3A`
- Final HTTP status: `200`
- `PAYMENT-RESPONSE`: `success=true`

## Investigation evidence

- Mint: `SKRbvo6Gf7GondiT3BbTfuRDPqLWei4j2Qy2NPGZhW3`
- Public response schema version: `1.0`
- Provider: `alpha_hunter`
- Intelligence source: real Alpha Hunter response through the read-only bridge
- Intelligence status: `STALE`
- Bridge mode: read-only
- Provider selection: no fallback to `stub`

## Verified flow

```text
Pera Wallet
  -> x402 payment signature
  -> Algorand Testnet facilitator settlement
  -> satellite API
  -> AlphaHunterProvider
  -> Alpha Hunter read-only bridge
  -> Alpha Hunter intelligence
  -> HTTP 200 response
```

No seed phrase, private key, bridge service token, or other secret is recorded
in this document. No additional payment was made while recording this evidence.
