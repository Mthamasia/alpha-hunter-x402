# Public Testnet Paid E2E — PASS

Recorded: 2026-09-24 (America/Sao_Paulo)

This document records the user-confirmed public end-to-end Testnet payment
completed against the Railway deployment. It contains no service token, wallet
secret, seed phrase, private key, or credential.

## Result

The public flow completed successfully:

```text
Pera Wallet
  -> x402 V2
  -> Algorand Testnet USDC settlement
  -> Railway public API
  -> AlphaHunterProvider
  -> authenticated Cloudflare bridge
  -> read-only Alpha Hunter intelligence
  -> HTTP 200
```

- Public API: `https://alpha-hunter-x402-production.up.railway.app`
- Bridge: `https://x402-bridge.memnx.com`
- HTTP final: `200`
- `PAYMENT-RESPONSE`: `success=true`
- API schema version: `1.0`
- Chain: `solana`
- Mint: `SKRbvo6Gf7GondiT3BbTfuRDPqLWei4j2Qy2NPGZhW3`
- Intelligence status: `STALE`
- Real Alpha Hunter intelligence: returned, including creator data

`STALE` is a real Alpha Hunter result and was preserved by the public API
without being converted to `FRESH`.

## Payment parameters validated before signing

- Network: Algorand Testnet
- CAIP-2: `algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI=`
- Asset: USDC Testnet
- ASA ID: `10458941`
- Amount: `50000` atomic units (`0.05 USDC`)
- Receiver: `A5D55SSWZFKTMSZ2CCCOMDVM3J4ROZT2Z2KAKXM26WQEVSLVSM3ZEEPKMQ`
- Public payer: `3KSUELDHDB73AVCRNQSJD27HVCAIET4LT34FPM6L25IQ34IMZMJM2D4V7E`
- Facilitator: `https://facilitator.goplausible.xyz`

## Local-evidence availability

The integration client displays the transaction identifier and request ID only
in its live browser state. No persisted local client log or session artifact
for this public E2E contained those values when this record was created.

- Transaction ID: not recoverable from local evidence
- Request ID: not recoverable from local evidence
- Transaction timestamp: not recoverable from local evidence

No additional request, signature, transaction, or payment was made to recover
these fields.

## Read-only bridge properties

The bridge health check confirmed:

```json
{
  "status": "ok",
  "service": "ahx-x402-bridge",
  "schema_version": "1.0",
  "database_read_only": true
}
```
