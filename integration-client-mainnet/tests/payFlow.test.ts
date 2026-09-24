import { encodePaymentResponseHeader } from "@x402/core/http";
import type { PaymentRequirements, SchemeNetworkClient } from "@x402/core/types";
import { describe, expect, it } from "vitest";

import { EXPECTED } from "../src/config";
import { PaymentSession, type FetchLike } from "../src/payFlow";
import { buildHttpClient } from "../src/x402";
import { PAYER, WRONG_PAY_TO, header402, paymentRequired, requirement, response } from "./fixtures";

const URL = "/api/v1/token/investigate";
const MINT = "So11111111111111111111111111111111111111112";

/** Scheme falso (sem rede, sem chave): conta quantas vezes a "assinatura" foi pedida. */
class FakeScheme implements SchemeNetworkClient {
  readonly scheme = "exact";
  calls: PaymentRequirements[] = [];
  constructor(private readonly fail = false) {}
  async createPaymentPayload(x402Version: number, req: PaymentRequirements) {
    this.calls.push(req);
    if (this.fail) throw new Error("User rejected the request");
    return { x402Version, payload: { paymentGroup: ["AAAA"], paymentIndex: 1 } };
  }
}

function fakeFetch(responses: Response[]) {
  const calls: { url: string; init: RequestInit }[] = [];
  const fn: FetchLike = async (url, init) => {
    calls.push({ url, init });
    const r = responses.shift();
    if (!r) throw new Error("unexpected extra request");
    return r;
  };
  return { fn, calls };
}

const settle = {
  success: true,
  transaction: "TESTTXID123",
  network: EXPECTED.network,
  payer: PAYER,
};

describe("fluxo manual x402", () => {
  it("cotação: POST sem pagamento -> 402 validado, nada assinado", async () => {
    const f = fakeFetch([response(402, {}, { "PAYMENT-REQUIRED": header402() })]);
    const s = new PaymentSession({ fetch: f.fn, url: URL, payer: PAYER });
    const q = await s.requestQuote(MINT);
    expect(q.kind).toBe("payable");
    expect(f.calls).toHaveLength(1);
    expect(new Headers(f.calls[0]!.init.headers).get("PAYMENT-SIGNATURE")).toBeNull();
    expect(JSON.parse(f.calls[0]!.init.body as string)).toEqual({ chain: "solana", mint: MINT });
    expect(s.currentState).toBe("quoted");
  });

  it("pagamento completo: 1 assinatura, 1 reenvio com PAYMENT-SIGNATURE, settlement exibido", async () => {
    const f = fakeFetch([
      response(402, {}, { "PAYMENT-REQUIRED": header402() }),
      response(200, { status: "NO_DATA_SOURCE" }, { "PAYMENT-RESPONSE": encodePaymentResponseHeader(settle as never) }),
    ]);
    const scheme = new FakeScheme();
    const s = new PaymentSession({ fetch: f.fn, url: URL, payer: PAYER });
    await s.requestQuote(MINT);
    const out = await s.pay(buildHttpClient(scheme), true);

    expect(scheme.calls).toHaveLength(1);
    expect(scheme.calls[0]).toMatchObject({ amount: "50000", asset: "31566704", payTo: EXPECTED.payTo });
    expect(f.calls).toHaveLength(2);
    const sig = new Headers(f.calls[1]!.init.headers).get("PAYMENT-SIGNATURE");
    expect(sig).toBeTruthy();
    const sent = JSON.parse(atob(sig!));
    expect(sent.x402Version).toBe(2);
    expect(sent.accepted).toMatchObject({ network: EXPECTED.network, amount: "50000", payTo: EXPECTED.payTo });
    expect(out.httpStatus).toBe(200);
    expect(out.transactionId).toBe("TESTTXID123");
    expect(out.settlement?.success).toBe(true);
    expect(out.error).toBeNull();
  });

  it("sem clique explícito não há pagamento", async () => {
    const f = fakeFetch([response(402, {}, { "PAYMENT-REQUIRED": header402() })]);
    const scheme = new FakeScheme();
    const s = new PaymentSession({ fetch: f.fn, url: URL, payer: PAYER });
    await s.requestQuote(MINT);
    await expect(s.pay(buildHttpClient(scheme), false)).rejects.toThrow(/clique explícito/);
    await expect(s.pay(buildHttpClient(scheme), undefined as unknown as boolean)).rejects.toThrow();
    expect(scheme.calls).toHaveLength(0);
    expect(f.calls).toHaveLength(1);
  });

  it("sem cotação validada não há pagamento", async () => {
    const scheme = new FakeScheme();
    const s = new PaymentSession({ fetch: fakeFetch([]).fn, url: URL, payer: PAYER });
    await expect(s.pay(buildHttpClient(scheme), true)).rejects.toThrow(/bloqueado/);
    expect(scheme.calls).toHaveLength(0);
  });

  it("402 divergente (typo no payTo) -> recusado; pagar é impossível", async () => {
    const bad = header402(paymentRequired([requirement({ payTo: WRONG_PAY_TO })]));
    const f = fakeFetch([response(402, {}, { "PAYMENT-REQUIRED": bad })]);
    const scheme = new FakeScheme();
    const s = new PaymentSession({ fetch: f.fn, url: URL, payer: PAYER });
    const q = await s.requestQuote(MINT);
    expect(q.kind).toBe("rejected");
    await expect(s.pay(buildHttpClient(scheme), true)).rejects.toThrow();
    expect(scheme.calls).toHaveLength(0);
  });

  it("uma tentativa por cotação: segundo clique (mesmo concorrente) é bloqueado", async () => {
    const f = fakeFetch([
      response(402, {}, { "PAYMENT-REQUIRED": header402() }),
      response(200, {}, { "PAYMENT-RESPONSE": encodePaymentResponseHeader(settle as never) }),
    ]);
    const scheme = new FakeScheme();
    const s = new PaymentSession({ fetch: f.fn, url: URL, payer: PAYER });
    await s.requestQuote(MINT);
    const first = s.pay(buildHttpClient(scheme), true);
    await expect(s.pay(buildHttpClient(scheme), true)).rejects.toThrow(/bloqueado/);
    await first;
    await expect(s.pay(buildHttpClient(scheme), true)).rejects.toThrow(/bloqueado/);
    expect(scheme.calls).toHaveLength(1);
    expect(f.calls).toHaveLength(2);
  });

  it("rejeição na carteira -> erro, nenhum reenvio, nenhum retry", async () => {
    const f = fakeFetch([response(402, {}, { "PAYMENT-REQUIRED": header402() })]);
    const scheme = new FakeScheme(true);
    const s = new PaymentSession({ fetch: f.fn, url: URL, payer: PAYER });
    await s.requestQuote(MINT);
    const out = await s.pay(buildHttpClient(scheme), true);
    expect(out.error).toMatch(/rejected/);
    expect(out.httpStatus).toBe(0);
    expect(f.calls).toHaveLength(1);
    expect(s.currentState).toBe("finished");
  });

  it("402 após pagamento (verify/settle falhou) -> exibe erro e NÃO repete", async () => {
    const f = fakeFetch([
      response(402, {}, { "PAYMENT-REQUIRED": header402() }),
      response(402, {}, { "PAYMENT-REQUIRED": header402(paymentRequired(undefined, { error: "invalid_exact_avm_payload_invalid_signature" })) }),
    ]);
    const s = new PaymentSession({ fetch: f.fn, url: URL, payer: PAYER });
    await s.requestQuote(MINT);
    const out = await s.pay(buildHttpClient(new FakeScheme()), true);
    expect(out.httpStatus).toBe(402);
    expect(out.error).toMatch(/invalid_exact_avm_payload_invalid_signature/);
    expect(out.transactionId).toBeNull();
    expect(f.calls).toHaveLength(2);
  });

  it("spend controls da biblioteca barram amount acima de 50000 (segunda trava)", async () => {
    const scheme = new FakeScheme();
    const http = buildHttpClient(scheme);
    await expect(http.createPaymentPayload(paymentRequired([requirement({ amount: "50001" })]))).rejects.toThrow();
    expect(scheme.calls).toHaveLength(0);
    await http.createPaymentPayload(paymentRequired());
    expect(scheme.calls).toHaveLength(1);
  });

  it("servidor sem cobrança (200 direto) não dispara pagamento", async () => {
    const f = fakeFetch([response(200, { status: "NO_DATA_SOURCE" })]);
    const s = new PaymentSession({ fetch: f.fn, url: URL, payer: PAYER });
    expect((await s.requestQuote(MINT)).kind).toBe("no-payment-required");
    await expect(s.pay(buildHttpClient(new FakeScheme()), true)).rejects.toThrow();
  });
});
