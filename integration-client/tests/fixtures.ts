import { encodePaymentRequiredHeader } from "@x402/core/http";
import type { PaymentRequired, PaymentRequirements } from "@x402/core/types";
import algosdk from "algosdk";

import { EXPECTED, MAINNET_GENESIS_HASH } from "../src/config";

/** Endereços públicos (sem chaves). */
export const PAYER = "3KSUELDHDB73AVCRNQSJD27HVCAIET4LT34FPM6L25IQ34IMZMJM2D4V7E";
export const FEE_PAYER = "ZMFK2OI7ZBD2U27ISERZC4S6LKM6WMFJPZQ4MYNJDZ2VNBNMBA67RA22AA";
/** payTo com erro de digitação que apareceu no prompt da Fase 3 (segmento duplicado). */
export const TYPO_PAY_TO =
  "A5D55SSWZFKTMSZ2CCCOMDVM3J4ROZT2Z2KAKXM26WQEVSLVSM3J4ROZT2Z2KAKXM26WQEVSLVSM3ZEEPKMQ";

export function requirement(over: Partial<PaymentRequirements> = {}): PaymentRequirements {
  return {
    scheme: "exact",
    network: EXPECTED.network,
    asset: EXPECTED.asset,
    amount: EXPECTED.amount,
    payTo: EXPECTED.payTo,
    maxTimeoutSeconds: 300,
    extra: {
      decimals: 6,
      feePayer: FEE_PAYER,
      genesisHash: EXPECTED.genesisHash,
      genesisId: EXPECTED.genesisId,
    },
    ...over,
  } as PaymentRequirements;
}

export function paymentRequired(accepts: PaymentRequirements[] = [requirement()], over: Partial<PaymentRequired> = {}): PaymentRequired {
  return {
    x402Version: 2,
    error: "Payment required",
    resource: { url: "http://127.0.0.1:8000/v1/token/investigate", description: "", mimeType: "application/json" },
    accepts,
    ...over,
  } as PaymentRequired;
}

export function header402(pr: PaymentRequired = paymentRequired()): string {
  return encodePaymentRequiredHeader(pr);
}

export function response(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

// ---------------------------------------------------------------- transações (offline)

function params(genesisHash: string = EXPECTED.genesisHash, genesisID: string = EXPECTED.genesisId) {
  return {
    fee: 0n,
    minFee: 1000n,
    firstValid: 1000n,
    lastValid: 1100n, // janela de 100 rounds (VALIDITY_WINDOW_ROUNDS)
    genesisHash: algosdk.base64ToBytes(genesisHash),
    genesisID,
    flatFee: true,
  };
}

export interface TxnOverrides {
  amount?: bigint;
  assetIndex?: bigint;
  receiver?: string;
  sender?: string;
  mainnet?: boolean;
  rekeyTo?: string;
  closeRemainderTo?: string;
}

/** Grupo no mesmo formato que o ExactAvmScheme monta: [feePayer pay, axfer do payer]. */
export function buildGroup(o: TxnOverrides = {}): { txns: algosdk.Transaction[]; encoded: Uint8Array[] } {
  const sp = o.mainnet ? params(MAINNET_GENESIS_HASH, "mainnet-v1.0") : params();
  const feeTxn = algosdk.makePaymentTxnWithSuggestedParamsFromObject({
    sender: FEE_PAYER,
    receiver: FEE_PAYER,
    amount: 0n,
    suggestedParams: { ...sp, fee: 2000n },
  });
  const axfer = algosdk.makeAssetTransferTxnWithSuggestedParamsFromObject({
    sender: o.sender ?? PAYER,
    receiver: o.receiver ?? EXPECTED.payTo,
    amount: o.amount ?? BigInt(EXPECTED.amount),
    assetIndex: o.assetIndex ?? BigInt(EXPECTED.asset),
    suggestedParams: sp,
    rekeyTo: o.rekeyTo,
    closeRemainderTo: o.closeRemainderTo,
  });
  const txns = algosdk.assignGroupID([feeTxn, axfer]);
  return { txns, encoded: txns.map((t) => algosdk.encodeUnsignedTransaction(t)) };
}

/** "Assinatura" falsa (64 bytes zero): só para exercitar o encoding, nunca é válida on-chain. */
export function fakeSign(txn: algosdk.Transaction): Uint8Array {
  return algosdk.encodeMsgpack(new algosdk.SignedTransaction({ txn, sig: new Uint8Array(64) }));
}

/** Pera falsa: registra chamadas e devolve apenas as transações com signers != []. */
export class FakePera {
  calls: { txn: algosdk.Transaction; signers?: string[] }[][][] = [];
  constructor(private readonly behavior: "sign" | "reject" | "tamper" | "extra" = "sign") {}

  async signTransaction(groups: { txn: algosdk.Transaction; signers?: string[] }[][]): Promise<Uint8Array[]> {
    this.calls.push(groups);
    if (this.behavior === "reject") throw new Error("User rejected the request");
    const toSign = groups.flat().filter((g) => !g.signers || g.signers.length > 0);
    if (this.behavior === "tamper") {
      const other = buildGroup({ amount: 1n }).txns[1]!;
      return [fakeSign(other)];
    }
    const signed = toSign.map((g) => fakeSign(g.txn));
    return this.behavior === "extra" ? [...signed, ...signed] : signed;
  }
}

/** Relógio de rounds controlado: grupo das fixtures é válido em [1000, 1100]. */
export const FIRST_VALID = 1000n;
export const GUARD = { getLastRound: async () => FIRST_VALID };
