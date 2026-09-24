/**
 * Reprodução e correção do "txn dead: round X outside of [firstValid, lastValid]".
 *
 * Usa o ExactAvmScheme REAL (@x402/avm) e o AlgorandClient REAL (algokit-utils)
 * com suggested params fixados via setSuggestedParamsCache (API oficial): nenhuma
 * chamada de rede, nenhuma chave, nenhum fundo. O tempo de aprovação na Pera é
 * simulado avançando um relógio de rounds.
 */
import { AlgorandClient } from "@algorandfoundation/algokit-utils/algorand-client";
import { ExactAvmScheme } from "@x402/avm/exact/client";
import algosdk from "algosdk";
import { describe, expect, it } from "vitest";

import { EXPECTED, MIN_ROUNDS_AFTER_SIGN, MIN_ROUNDS_BEFORE_SIGN, VALIDITY_WINDOW_ROUNDS } from "../src/config";
import { PaymentSession, type FetchLike } from "../src/payFlow";
import { createPeraSigner } from "../src/peraSigner";
import { buildHttpClient, createMainnetAlgorand } from "../src/x402";
import { PAYER, fakeSign, header402, requirement, response } from "./fixtures";

const ROUND = 60_000_000n;
const SECONDS_PER_ROUND = 2.8; // Mainnet aprox.

function freezeParams(algorand: AlgorandClient): AlgorandClient {
  return algorand.setSuggestedParamsCache(
    {
      consensusVersion: "mainnet",
      fee: 0n,
      minFee: 1000n,
      flatFee: false,
      firstValid: ROUND,
      lastValid: ROUND + 1000n,
      genesisId: EXPECTED.genesisId,
      genesisHash: algosdk.base64ToBytes(EXPECTED.genesisHash),
    },
    new Date(Date.now() + 3_600_000),
  );
}

/** Pera falsa com relógio: a "aprovação humana" consome `approvalSeconds`. */
function clockedPera(approvalSeconds: number, clock: { round: bigint }) {
  const calls: algosdk.Transaction[][] = [];
  return {
    calls,
    async signTransaction(groups: { txn: algosdk.Transaction; signers?: string[] }[][]) {
      calls.push(groups[0]!.map((g) => g.txn));
      clock.round += BigInt(Math.ceil(approvalSeconds / SECONDS_PER_ROUND));
      return groups.flat().filter((g) => g.signers?.length).map((g) => fakeSign(g.txn));
    },
  };
}

function setup(algorand: AlgorandClient, approvalSeconds: number, clockStart = ROUND) {
  const clock = { round: clockStart };
  const pera = clockedPera(approvalSeconds, clock);
  const signer = createPeraSigner(pera, PAYER, { getLastRound: async () => clock.round });
  const scheme = new ExactAvmScheme(signer, { algorandClient: freezeParams(algorand) });
  return { clock, pera, scheme };
}

describe("causa raiz: janela padrão de 10 rounds", () => {
  it("sem configuração, o ExactAvmScheme monta o grupo com lastValid = firstValid + 10", async () => {
    // Captura o grupo sem assinar (Pera recusa na hora).
    let captured: algosdk.Transaction[] = [];
    const signer = {
      address: PAYER,
      async signTransactions(txns: Uint8Array[]) {
        captured = txns.map((t) => algosdk.decodeUnsignedTransaction(t));
        throw new Error("captured");
      },
    };
    const scheme = new ExactAvmScheme(signer, { algorandClient: freezeParams(AlgorandClient.mainNet()) });
    await expect(scheme.createPaymentPayload(2, requirement())).rejects.toThrow("captured");

    expect(captured).toHaveLength(2);
    for (const txn of captured) {
      expect(txn.firstValid).toBe(ROUND); // obtido na montagem, logo antes de assinar
      expect(txn.lastValid).toBe(ROUND + 10n); // janela padrão do TransactionComposer
    }
    // 40 s de aprovação na Pera (~15 rounds) -> round atual > lastValid -> "txn dead".
    const roundAtSettle = ROUND + BigInt(Math.ceil(40 / SECONDS_PER_ROUND));
    expect(roundAtSettle > captured[1]!.lastValid).toBe(true);
  });

  it("com a janela padrão, o guard recusa ANTES de abrir a Pera (nada é assinado)", async () => {
    const { pera, scheme } = setup(AlgorandClient.mainNet(), 40);
    await expect(scheme.createPaymentPayload(2, requirement())).rejects.toThrow(/antes da assinatura/);
    expect(pera.calls).toHaveLength(0);
  });
});

describe("correção: AlgorandClient com setDefaultValidityWindow(100)", () => {
  it("grupo fresco com janela de 100 rounds; aprovação de 40 s é aceita", async () => {
    const { pera, scheme } = setup(createMainnetAlgorand(), 40);
    const result = await scheme.createPaymentPayload(2, requirement());

    expect(pera.calls).toHaveLength(1);
    const payload = result.payload as { paymentGroup: string[]; paymentIndex: number };
    expect(payload.paymentGroup).toHaveLength(2);
    expect(payload.paymentIndex).toBe(1);
    for (const txn of pera.calls[0]!) {
      expect(txn.firstValid).toBe(ROUND);
      expect(txn.lastValid).toBe(ROUND + BigInt(VALIDITY_WINDOW_ROUNDS));
    }
    const signed = algosdk.decodeSignedTransaction(algosdk.base64ToBytes(payload.paymentGroup[1]!));
    expect(signed.txn.assetTransfer?.amount).toBe(50000n);
    expect(signed.txn.assetTransfer?.assetIndex).toBe(31566704n);
    expect(signed.txn.assetTransfer?.receiver.toString()).toBe(EXPECTED.payTo);
    expect(signed.txn.genesisID).toBe("mainnet-v1.0");
  });

  it("aprovação lenta demais -> recusado APÓS assinar, sem produzir payload", async () => {
    const slow = (VALIDITY_WINDOW_ROUNDS - MIN_ROUNDS_AFTER_SIGN + 1) * SECONDS_PER_ROUND;
    const { pera, scheme } = setup(createMainnetAlgorand(), slow);
    await expect(scheme.createPaymentPayload(2, requirement())).rejects.toThrow(/após a assinatura/);
    expect(pera.calls).toHaveLength(1);
  });

  it("grupo já velho quando a Pera iria abrir -> recusado ANTES da assinatura", async () => {
    // Relógio já está além do firstValid (ex.: montagem atrasada).
    const stale = ROUND + BigInt(VALIDITY_WINDOW_ROUNDS - MIN_ROUNDS_BEFORE_SIGN + 1);
    const { pera, scheme } = setup(createMainnetAlgorand(), 10, stale);
    await expect(scheme.createPaymentPayload(2, requirement())).rejects.toThrow(/antes da assinatura/);
    expect(pera.calls).toHaveLength(0);
  });

  it("janela maior que a configurada não é aceita (não mascara com lastValid arbitrário)", async () => {
    const { pera, scheme } = setup(AlgorandClient.mainNet().setDefaultValidityWindow(1000), 10);
    await expect(scheme.createPaymentPayload(2, requirement())).rejects.toThrow(/Janela de validade inesperada/);
    expect(pera.calls).toHaveLength(0);
  });

  it("a janela cabe no maxTimeoutSeconds anunciado pelo servidor (300 s)", () => {
    expect(VALIDITY_WINDOW_ROUNDS * SECONDS_PER_ROUND).toBeLessThanOrEqual(requirement().maxTimeoutSeconds);
    expect(VALIDITY_WINDOW_ROUNDS).toBeLessThanOrEqual(1000);
  });
});

describe("fluxo completo: payload expirado nunca é enviado nem reenviado", () => {
  function fakeFetch(responses: Response[]) {
    const calls: RequestInit[] = [];
    const fn: FetchLike = async (_u, init) => {
      calls.push(init);
      const r = responses.shift();
      if (!r) throw new Error("unexpected extra request");
      return r;
    };
    return { fn, calls };
  }

  it("aprovação lenta: erro exibido, 1 única requisição (a cotação), sem retry", async () => {
    const f = fakeFetch([response(402, {}, { "PAYMENT-REQUIRED": header402() })]);
    const { pera, scheme } = setup(createMainnetAlgorand(), 300);
    const s = new PaymentSession({ fetch: f.fn, url: "/api/v1/token/investigate", payer: PAYER });
    await s.requestQuote("So11111111111111111111111111111111111111112");
    const out = await s.pay(buildHttpClient(scheme), true);

    expect(out.error).toMatch(/após a assinatura/);
    expect(out.httpStatus).toBe(0);
    expect(f.calls).toHaveLength(1); // nenhum PAYMENT-SIGNATURE enviado
    expect(pera.calls).toHaveLength(1); // uma única assinatura pedida
    await expect(s.pay(buildHttpClient(scheme), true)).rejects.toThrow(/bloqueado/);
  });

  it("aprovação normal: 1 cotação + 1 envio com PAYMENT-SIGNATURE", async () => {
    const f = fakeFetch([
      response(402, {}, { "PAYMENT-REQUIRED": header402() }),
      response(200, { status: "NO_DATA_SOURCE" }),
    ]);
    const { pera, scheme } = setup(createMainnetAlgorand(), 40);
    const s = new PaymentSession({ fetch: f.fn, url: "/api/v1/token/investigate", payer: PAYER });
    await s.requestQuote("So11111111111111111111111111111111111111112");
    const out = await s.pay(buildHttpClient(scheme), true);

    expect(out.httpStatus).toBe(200);
    expect(f.calls).toHaveLength(2);
    expect(new Headers(f.calls[1]!.headers).get("PAYMENT-SIGNATURE")).toBeTruthy();
    expect(pera.calls).toHaveLength(1);
  });
});
