import { describe, expect, it } from "vitest";

import { EXPECTED, MAINNET_GENESIS_HASH } from "../src/config";
import { formatAtomic, parsePaymentRequired, validatePaymentRequired } from "../src/validate";
import { PAYER, TYPO_PAY_TO, header402, paymentRequired, requirement } from "./fixtures";

describe("parsing do 402 (PAYMENT-REQUIRED x402 V2)", () => {
  it("decodifica o header com o decoder oficial", () => {
    const pr = parsePaymentRequired(header402());
    expect(pr.x402Version).toBe(2);
    expect(pr.accepts[0]).toMatchObject({
      scheme: "exact",
      network: EXPECTED.network,
      asset: "10458941",
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
      networkLabel: "Algorand Testnet",
      assetId: "10458941",
      amountAtomic: "50000",
      amountUsdc: "0.05",
      payTo: "A5D55SSWZFKTMSZ2CCCOMDVM3J4ROZT2Z2KAKXM26WQEVSLVSM3ZEEPKMQ",
      payer: PAYER,
    });
  });

  const cases: [string, Parameters<typeof requirement>[0]][] = [
    ["network mainnet", { network: `algorand:${MAINNET_GENESIS_HASH}` }],
    ["network v1 alias", { network: "algorand-testnet" as never }],
    ["network outra chain", { network: "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1" }],
    ["ASA diferente", { asset: "31566704" }],
    ["ASA vazio", { asset: "" }],
    ["amount maior", { amount: "50001" }],
    ["amount menor", { amount: "49999" }],
    ["amount em USD", { amount: "0.05" }],
    ["payTo com typo do prompt", { payTo: TYPO_PAY_TO }],
    ["payTo diferente", { payTo: PAYER }],
    ["scheme diferente", { scheme: "upto" }],
    ["genesisHash mainnet no extra", { extra: { decimals: 6, genesisHash: MAINNET_GENESIS_HASH } }],
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
  });

  it("o payTo esperado é o PAY_TO da Fase 2, não o valor com typo", () => {
    expect(EXPECTED.payTo).toBe("A5D55SSWZFKTMSZ2CCCOMDVM3J4ROZT2Z2KAKXM26WQEVSLVSM3ZEEPKMQ");
    expect(EXPECTED.payTo).not.toBe(TYPO_PAY_TO);
    expect(EXPECTED.payTo).toHaveLength(58);
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
