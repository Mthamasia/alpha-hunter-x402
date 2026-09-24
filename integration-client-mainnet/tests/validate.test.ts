import { describe, expect, it } from "vitest";

import { EXPECTED, TESTNET_GENESIS_HASH } from "../src/config";
import { formatAtomic, parsePaymentRequired, validatePaymentRequired } from "../src/validate";
import { PAYER, WRONG_PAY_TO, header402, paymentRequired, requirement } from "./fixtures";

describe("parsing do 402 (PAYMENT-REQUIRED x402 V2)", () => {
  it("decodifica o header com o decoder oficial", () => {
    const pr = parsePaymentRequired(header402());
    expect(pr.x402Version).toBe(2);
    expect(pr.accepts[0]).toMatchObject({
      scheme: "exact",
      network: EXPECTED.network,
      asset: "31566704",
      amount: "50000",
      payTo: EXPECTED.payTo,
    });
  });

  it("decodifica o 402 real capturado na Fase 2", () => {
    // Header produzido pelo resource server Python (x402-avm 2.0.2) — mesmos campos.
    const pr = parsePaymentRequired(header402(paymentRequired()));
    expect(validatePaymentRequired(pr, PAYER).ok).toBe(true);
  });

  it("falha sem header ou com header inválido", () => {
    expect(() => parsePaymentRequired(null)).toThrow(/PAYMENT-REQUIRED/);
    expect(() => parsePaymentRequired("")).toThrow();
    expect(() => parsePaymentRequired("!!!not-base64")).toThrow();
  });
});

describe("validação estrita antes de assinar", () => {
  const ok = () => validatePaymentRequired(paymentRequired(), PAYER);

  it("aceita exatamente os valores esperados e resume para a UI", () => {
    const r = ok();
    expect(r.ok).toBe(true);
    expect(r.summary).toEqual({
      network: EXPECTED.network,
      networkLabel: "Algorand Mainnet",
      assetId: "31566704",
      amountAtomic: "50000",
      amountUsdc: "0.05",
      payTo: "ROO3X2KFNJVXIT2ZA6MFYOV7FRRZNVH2WIPO6TDWFVNEHVMQGFT6O4KFLA",
      payer: PAYER,
    });
  });

  const cases: [string, Parameters<typeof requirement>[0]][] = [
    ["network testnet", { network: `algorand:${TESTNET_GENESIS_HASH}` }],
    ["network v1 alias", { network: "algorand-mainnet" as never }],
    ["network outra chain", { network: "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1" }],
    ["ASA diferente", { asset: "10458941" }],
    ["ASA vazio", { asset: "" }],
    ["amount maior", { amount: "50001" }],
    ["amount menor", { amount: "49999" }],
    ["amount em USD", { amount: "0.05" }],
    ["payTo diferente", { payTo: WRONG_PAY_TO }],
    ["payTo diferente", { payTo: PAYER }],
    ["scheme diferente", { scheme: "upto" }],
    ["genesisHash testnet no extra", { extra: { decimals: 6, genesisHash: TESTNET_GENESIS_HASH } }],
    ["decimals diferente", { extra: { decimals: 2 } }],
  ];
  for (const [name, over] of cases) {
    it(`bloqueia: ${name}`, () => {
      const r = validatePaymentRequired(paymentRequired([requirement(over)]), PAYER);
      expect(r.ok).toBe(false);
      if (!r.ok) expect(r.errors.length).toBeGreaterThan(0);
    });
  }

  it("bloqueia x402Version ≠ 2", () => {
    expect(validatePaymentRequired(paymentRequired(undefined, { x402Version: 1 }), PAYER).ok).toBe(false);
  });

  it("bloqueia múltiplas opções (mesmo que uma seja válida) e lista vazia", () => {
    const two = paymentRequired([requirement(), requirement({ amount: "1" })]);
    expect(validatePaymentRequired(two, PAYER).ok).toBe(false);
    expect(validatePaymentRequired(paymentRequired([]), PAYER).ok).toBe(false);
  });

  it("bloqueia sem carteira conectada ou payer == payTo", () => {
    expect(validatePaymentRequired(paymentRequired(), "").ok).toBe(false);
    expect(validatePaymentRequired(paymentRequired(), EXPECTED.payTo).ok).toBe(false);
    expect(validatePaymentRequired(paymentRequired(), WRONG_PAY_TO).ok).toBe(false);
  });

  it("o payTo e payer esperados são os valores Mainnet aprovados", () => {
    expect(EXPECTED.payTo).toBe("ROO3X2KFNJVXIT2ZA6MFYOV7FRRZNVH2WIPO6TDWFVNEHVMQGFT6O4KFLA");
    expect(EXPECTED.payTo).not.toBe(WRONG_PAY_TO);
    expect(EXPECTED.payTo).toHaveLength(58);
  });

  it("bloqueia Bazaar ou challenge tag ausentes", () => {
    expect(validatePaymentRequired(paymentRequired(undefined, { extensions: {} }), PAYER).ok).toBe(false);
    expect(validatePaymentRequired(
      paymentRequired(undefined, { extensions: { bazaar: { info: { tags: [] } } } }),
      PAYER,
    ).ok).toBe(false);
  });
});

describe("formatAtomic", () => {
  it("converte sem float", () => {
    expect(formatAtomic("50000", 6)).toBe("0.05");
    expect(formatAtomic("1", 6)).toBe("0.000001");
    expect(formatAtomic("1000000", 6)).toBe("1");
    expect(formatAtomic("abc", 6)).toBe("inválido");
  });
});
