/**
 * Fluxo manual x402 V2: cotação (402) -> validação -> clique explícito ->
 * assinatura na Pera -> UMA reenvio com PAYMENT-SIGNATURE. Sem retry automático.
 */
import type { PaymentPayload, PaymentRequired, SettleResponse } from "@x402/core/types";

import { type PaymentSummary, parsePaymentRequired, validatePaymentRequired, PAYMENT_REQUIRED_HEADER } from "./validate";

/** Subconjunto real de x402HTTPClient (@x402/core/client) usado aqui. */
export interface X402HttpLike {
  createPaymentPayload(paymentRequired: PaymentRequired): Promise<PaymentPayload>;
  encodePaymentSignatureHeader(paymentPayload: PaymentPayload): Record<string, string>;
  getPaymentSettleResponse(getHeader: (name: string) => string | null | undefined): SettleResponse;
}

export type FetchLike = (url: string, init: RequestInit) => Promise<Response>;

export type QuoteOutcome =
  | { kind: "payable"; summary: PaymentSummary }
  | { kind: "rejected"; summary: PaymentSummary | null; errors: string[] }
  | { kind: "no-payment-required"; status: number; body: unknown }
  | { kind: "error"; status: number; message: string; body: unknown };

export interface PayOutcome {
  httpStatus: number;
  body: unknown;
  settlement: SettleResponse | null;
  transactionId: string | null;
  error: string | null;
}

type State = "idle" | "quoting" | "quoted" | "paying" | "finished";

async function readBody(res: Response): Promise<unknown> {
  const text = await res.text();
  try {
    return text ? JSON.parse(text) : null;
  } catch {
    return text;
  }
}

export class PaymentSession {
  private state: State = "idle";
  private quote: { paymentRequired: PaymentRequired; body: string } | null = null;

  constructor(
    private readonly deps: { fetch: FetchLike; url: string; payer: string },
  ) {}

  get currentState(): State {
    return this.state;
  }

  /** Passo 1: chama o endpoint SEM pagamento e valida o 402. Nunca assina nada. */
  async requestQuote(mint: string): Promise<QuoteOutcome> {
    if (this.state !== "idle") throw new Error(`requestQuote inválido no estado ${this.state}`);
    this.state = "quoting";
    const body = JSON.stringify({ chain: "solana", mint });
    const res = await this.deps.fetch(this.deps.url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    });

    if (res.status !== 402) {
      this.state = "finished";
      const resBody = await readBody(res);
      return res.ok
        ? { kind: "no-payment-required", status: res.status, body: resBody }
        : { kind: "error", status: res.status, message: `HTTP ${res.status}`, body: resBody };
    }

    let pr: PaymentRequired;
    try {
      pr = parsePaymentRequired(res.headers.get(PAYMENT_REQUIRED_HEADER));
    } catch (e) {
      this.state = "finished";
      return { kind: "error", status: 402, message: (e as Error).message, body: null };
    }

    const v = validatePaymentRequired(pr, this.deps.payer);
    if (!v.ok) {
      this.state = "finished";
      return { kind: "rejected", summary: v.summary, errors: v.errors };
    }
    // Só a opção validada segue adiante: o selector do x402Client não tem outra escolha.
    this.quote = { paymentRequired: { ...pr, accepts: [v.requirement] }, body };
    this.state = "quoted";
    return { kind: "payable", summary: v.summary };
  }

  /**
   * Passo 2: SOMENTE a partir de um clique explícito. Executa no máximo uma vez
   * por sessão (a trava é tomada de forma síncrona, antes de qualquer await).
   */
  async pay(http: X402HttpLike, explicitUserClick: boolean): Promise<PayOutcome> {
    if (explicitUserClick !== true) throw new Error("Pagamento exige clique explícito do usuário.");
    if (this.state !== "quoted" || !this.quote) {
      throw new Error(`Pagamento bloqueado: estado ${this.state} (uma tentativa por cotação).`);
    }
    this.state = "paying"; // trava
    const { paymentRequired, body } = this.quote;
    this.quote = null;

    try {
      const payload = await http.createPaymentPayload(paymentRequired); // abre a Pera
      const headers = {
        "Content-Type": "application/json",
        ...http.encodePaymentSignatureHeader(payload),
      };
      const res = await this.deps.fetch(this.deps.url, { method: "POST", headers, body });
      const resBody = await readBody(res);

      let settlement: SettleResponse | null = null;
      try {
        settlement = http.getPaymentSettleResponse((n) => res.headers.get(n));
      } catch {
        settlement = null;
      }

      let error: string | null = null;
      if (!res.ok) {
        error = `HTTP ${res.status}`;
        const pr = res.headers.get(PAYMENT_REQUIRED_HEADER);
        if (pr) {
          try {
            error += `: ${parsePaymentRequired(pr).error ?? "payment required"}`;
          } catch {
            /* ignore */
          }
        }
      }
      return {
        httpStatus: res.status,
        body: resBody,
        settlement,
        transactionId: settlement?.transaction || null,
        error,
      };
    } catch (e) {
      return { httpStatus: 0, body: null, settlement: null, transactionId: null, error: (e as Error).message };
    } finally {
      this.state = "finished"; // nunca volta para "quoted": sem retry
    }
  }
}
