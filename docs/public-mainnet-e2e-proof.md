# Public Mainnet Paid E2E â€” PASS

Evidence date: 2026-09-24

This document records the user-confirmed, single controlled Mainnet x402
payment. It contains public transaction/account identifiers only. No private
key, seed phrase, mnemonic, bridge token, or credential is recorded.

## Result

```text
Pera Wallet
  -> x402 V2
  -> Algorand Mainnet USDC settlement
  -> Railway public API
  -> AlphaHunterProvider
  -> authenticated read-only Cloudflare bridge
  -> Alpha Hunter intelligence
  -> HTTP 200
```

- HTTP final: `200`
- `PAYMENT-RESPONSE`: `success=true`
- Transaction ID: `VZUFPPPV37C3CR45S3JGPHKENFX4GWZ6HWW5UDWM4WDVAI7O3COLA`
- Network: `algorand:wGHE2Pwdvd7S12BL5FaOP20EGYesN73ktiC1qzkkit8=`
- Asset: Algorand Mainnet USDC
- ASA ID: `31566704`
- Amount: `50000` atomic units (`0.05 USDC`)
- Payer: `3KSUELDHDB73AVCRNQSJD27HVCAIET4LT34FPM6L25IQ34IMZMJM2D4V7E`
- Receiver / payTo: `ROO3X2KFNJVXIT2ZA6MFYOV7FRRZNVH2WIPO6TDWFVNEHVMQGFT6O4KFLA`

## API response

- Provider: `alpha_hunter`
- Schema version: `1.0`
- Request ID: `c2f2bfdf-92c5-4be5-943a-4341dfa6e78d`
- Chain: `solana`
- Mint: `So11111111111111111111111111111111111111112`
- Generated at: `2026-09-24T23:18:27.427255Z`
- Status: `STALE`

The response contained real Alpha Hunter intelligence. For this mint, several
returned intelligence fields were `null`. Those values and the `STALE` status
are preserved as observed; they were not replaced, inferred, or represented as
`FRESH`.

## Execution controls

- One controlled Mainnet execution was authorized and completed.
- No retry was performed.
- No additional payment was performed to create this record.
- No wallet material was stored by the client or recorded in this repository.
- The Alpha Hunter bridge remained authenticated and read-only.
