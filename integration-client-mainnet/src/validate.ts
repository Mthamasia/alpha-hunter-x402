import { decodePaymentRequiredHeader } from "@x402/core/http";
import type { PaymentRequired, PaymentRequirements } from "@x402/core/types";

import { EXPECTED, TESTNET_GENESIS_HASH } from "./config";

export const PAYMENT_REQUIRED_HEADER = "PAYMENT-REQUIRED";

export interface PaymentSummary {
  network: string;
  networkLabel: string;
  assetId: string;
  amountAtomic: string;
  amountUsdc: string;
  payTo: string;
  payer: string;
}

export type ValidationResult =
  | { ok: true; requirement: PaymentRequirements; summary: PaymentSummary }
  | { ok: false; errors: string[]; summary: PaymentSummary | null };

/** Decodifica o header PAYMENT-REQUIRED (x402 V2) com o decoder oficial. */
export function parsePaymentRequired(headerValue: string | null | undefined): PaymentRequired {
  if (!headerValue) throw new Error("Resposta 402 sem header PAYMENT-REQUIRED.");
  return decodePaymentRequiredHeader(headerValue);
}

/** Formata unidades atômicas em decimal sem ponto flutuante. */
export function formatAtomic(amount: string, decimals: number): string {
  if (!/^\d+$/.test(amount)) return "inválido";
  const padded = amount.padStart(decimals + 1, "0");
  const int = padded.slice(0, -decimals);
  const frac = padded.slice(-decimals).replace(/0+$/, "");
  return frac ? `${int}.${frac}` : int;
}

function networkLabel(network: string): string {
  if (network === EXPECTED.network) return "Algorand Mainnet";
  if (network.includes(TESTNET_GENESIS_HASH)) return "Algorand TESTNET (bloqueada)";
  return "desconhecida";
}

function summarize(req: PaymentRequirements, payer: string): PaymentSummary {
  return {
    network: req.network,
    networkLabel: networkLabel(req.network),
    assetId: req.asset,
    amountAtomic: req.amount,
    amountUsdc: formatAtomic(req.amount, EXPECTED.decimals),
    payTo: req.payTo,
    payer,
  };
}

/**
 * Valida estritamente o 402 contra os valores esperados.
 * Exige exatamente UMA opção de pagamento, Bazaar discovery e valores imutáveis.
 */
export function validatePaymentRequired(pr: PaymentRequired, payer: string): ValidationResult {
  const errors: string[] = [];

  if (pr.x402Version !== EXPECTED.x402Version) {
    errors.push(`x402Version ${String(pr.x402Version)} ≠ ${EXPECTED.x402Version}`);
  }
  if (!Array.isArray(pr.accepts) || pr.accepts.length !== 1) {
    errors.push(`esperada exatamente 1 opção em accepts, recebidas ${pr.accepts?.length ?? 0}`);
    const first = pr.accepts?.[0];
    return { ok: false, errors, summary: first ? summarize(first, payer) : null };
  }

  const req = pr.accepts[0]!;
  const summary = summarize(req, payer);

  if (req.scheme !== EXPECTED.scheme) errors.push(`scheme "${req.scheme}" ≠ "${EXPECTED.scheme}"`);
  if (req.network !== EXPECTED.network) errors.push(`network "${req.network}" não é Algorand Mainnet`);
  if (req.network.includes(TESTNET_GENESIS_HASH)) errors.push("network é TESTNET — bloqueada");
  if (req.asset !== EXPECTED.asset) errors.push(`asset "${req.asset}" ≠ ${EXPECTED.asset}`);
  if (req.amount !== EXPECTED.amount) errors.push(`amount "${req.amount}" ≠ ${EXPECTED.amount}`);
  if (req.payTo !== EXPECTED.payTo) errors.push(`payTo "${req.payTo}" ≠ ${EXPECTED.payTo}`);

  const extra = (req.extra ?? {}) as Record<string, unknown>;
  if (extra.genesisHash !== undefined && extra.genesisHash !== EXPECTED.genesisHash) {
    errors.push(`extra.genesisHash não é Testnet`);
  }
  if (extra.decimals !== undefined && extra.decimals !== EXPECTED.decimals) {
    errors.push(`extra.decimals ${String(extra.decimals)} ≠ ${EXPECTED.decimals}`);
  }

  if (extra.feePayer !== EXPECTED.feePayer) errors.push("feePayer inesperado");
  const extensions = pr.extensions as Record<string, unknown> | undefined;
  const bazaar = extensions?.bazaar as { info?: { tags?: unknown } } | undefined;
  if (!bazaar) errors.push("Bazaar discovery metadata ausente");
  if (!Array.isArray(bazaar?.info?.tags) || !bazaar.info.tags.includes("x402-global-challenge")) {
    errors.push("tag x402-global-challenge ausente");
  }
  if (payer !== EXPECTED.payer) errors.push("payer não é a carteira Mainnet autorizada");

  return errors.length ? { ok: false, errors, summary } : { ok: true, requirement: req, summary };
}
