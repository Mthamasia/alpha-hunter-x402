import { PeraWalletConnect } from "@perawallet/connect";

import { EXPECTED, INVESTIGATE_PATH, PAY_BUTTON_LABEL, PERA_MAINNET_CHAIN_ID } from "./config";
import { PaymentSession, type PayOutcome, type QuoteOutcome } from "./payFlow";
import type { PaymentSummary } from "./validate";
import { buildPeraHttpClient } from "./x402";

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;

// Pera travada na Mainnet (chainId 416001). A chave nunca sai da Pera.
const pera = new PeraWalletConnect({ chainId: PERA_MAINNET_CHAIN_ID, shouldShowSignTxnToast: true });

let account = "";
let session: PaymentSession | null = null;
// Trava de página: esta página executa no máximo UMA tentativa de pagamento.
let paymentAttempted = false;

const ui = {
  connect: $<HTMLButtonElement>("connect"),
  disconnect: $<HTMLButtonElement>("disconnect"),
  account: $("account"),
  mint: $<HTMLInputElement>("mint"),
  quote: $<HTMLButtonElement>("quote"),
  quoteStatus: $("quote-status"),
  summary: $<HTMLTableSectionElement>("summary-body"),
  errors: $<HTMLUListElement>("errors"),
  pay: $<HTMLButtonElement>("pay"),
  payStatus: $("pay-status"),
  httpFinal: $("http-final"),
  apiResult: $("api-result"),
  settlement: $("settlement"),
  txid: $("txid"),
  payError: $("pay-error"),
};

ui.pay.textContent = PAY_BUTTON_LABEL;

function setAccount(addr: string) {
  account = addr;
  ui.account.textContent = addr || "(não conectado)";
  ui.connect.disabled = !!addr;
  ui.disconnect.disabled = !addr;
  resetQuote();
}

function resetQuote() {
  session = null;
  ui.pay.disabled = true;
  ui.summary.replaceChildren();
  ui.errors.replaceChildren();
  ui.quoteStatus.textContent = "";
  ui.quote.disabled = !account || paymentAttempted;
}

function row(label: string, value: string, expected?: string) {
  const tr = document.createElement("tr");
  const ok = expected === undefined ? null : value === expected;
  for (const text of [label, value, ok === null ? "" : ok ? "OK" : `ESPERADO: ${expected}`]) {
    const td = document.createElement("td");
    td.textContent = text;
    tr.append(td);
  }
  if (ok === false) tr.className = "bad";
  return tr;
}

function renderSummary(s: PaymentSummary | null) {
  ui.summary.replaceChildren();
  if (!s) return;
  ui.summary.append(
    row("Network", `${s.networkLabel} (${s.network})`, `Algorand Mainnet (${EXPECTED.network})`),
    row("Asset ID", s.assetId, EXPECTED.asset),
    row("Amount atomic", s.amountAtomic, EXPECTED.amount),
    row("Amount USDC", s.amountUsdc, "0.05"),
    row("payTo", s.payTo, EXPECTED.payTo),
    row("Payer", s.payer, EXPECTED.payer),
    row("Expected payer", EXPECTED.payer, EXPECTED.payer),
  );
}

function renderErrors(errs: string[]) {
  ui.errors.replaceChildren(
    ...errs.map((e) => Object.assign(document.createElement("li"), { textContent: e })),
  );
}

function renderPay(o: PayOutcome) {
  ui.httpFinal.textContent = o.httpStatus ? String(o.httpStatus) : "(sem resposta)";
  ui.apiResult.textContent = o.body === null ? "" : JSON.stringify(o.body, null, 2);
  ui.settlement.textContent = o.settlement ? JSON.stringify(o.settlement, null, 2) : "(sem PAYMENT-RESPONSE)";
  ui.txid.replaceChildren();
  if (o.transactionId) {
    const a = document.createElement("a");
    a.href = `https://lora.algokit.io/mainnet/transaction/${encodeURIComponent(o.transactionId)}`;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.textContent = o.transactionId;
    ui.txid.append(a);
  } else {
    ui.txid.textContent = "(não fornecido)";
  }
  ui.payError.textContent = o.error ?? "";
}

ui.connect.addEventListener("click", async () => {
  try {
    const accounts = await pera.connect();
    pera.connector?.on("disconnect", () => setAccount(""));
    setAccount(accounts[0] ?? "");
  } catch (e) {
    ui.account.textContent = `Falha ao conectar: ${(e as Error).message}`;
  }
});

ui.disconnect.addEventListener("click", async () => {
  await pera.disconnect();
  setAccount("");
});

ui.quote.addEventListener("click", async () => {
  if (!account || paymentAttempted) return;
  resetQuote();
  ui.quote.disabled = true;
  ui.quoteStatus.textContent = "Consultando (sem pagamento)...";
  session = new PaymentSession({ fetch: (u, i) => fetch(u, i), url: INVESTIGATE_PATH, payer: account });
  let q: QuoteOutcome;
  try {
    q = await session.requestQuote(ui.mint.value.trim());
  } catch (e) {
    ui.quoteStatus.textContent = `Erro: ${(e as Error).message}`;
    ui.quote.disabled = false;
    return;
  }
  ui.quote.disabled = false;
  switch (q.kind) {
    case "payable":
      renderSummary(q.summary);
      ui.quoteStatus.textContent = "402 recebido e VALIDADO. Revise os valores antes de pagar.";
      ui.pay.disabled = false;
      break;
    case "rejected":
      renderSummary(q.summary);
      renderErrors(q.errors);
      ui.quoteStatus.textContent = "402 RECUSADO: valores divergentes. Pagamento bloqueado.";
      break;
    case "no-payment-required":
      ui.quoteStatus.textContent = `HTTP ${q.status} sem cobrança (servidor não está em x402-mainnet?).`;
      ui.apiResult.textContent = JSON.stringify(q.body, null, 2);
      break;
    case "error":
      ui.quoteStatus.textContent = `Erro: ${q.message}`;
      ui.apiResult.textContent = q.body ? JSON.stringify(q.body, null, 2) : "";
      break;
  }
});

ui.pay.addEventListener("click", async (ev) => {
  // Trava síncrona: desabilita antes de qualquer await; um clique = uma tentativa.
  if (!ev.isTrusted || paymentAttempted || !session || !account) return;
  paymentAttempted = true;
  ui.pay.disabled = true;
  ui.quote.disabled = true;
  ui.payStatus.textContent = "Preparando transação...";

  let outcome: PayOutcome;
  try {
    const http = buildPeraHttpClient(pera, account, () => {
      ui.payStatus.textContent = "Aprove (ou rejeite) a assinatura na Pera Wallet...";
    });
    outcome = await session.pay(http, true);
  } catch (e) {
    outcome = { httpStatus: 0, body: null, settlement: null, transactionId: null, error: (e as Error).message };
  }
  ui.payStatus.textContent =
    outcome.error ? "Tentativa concluída com erro (sem retry automático)." : "Concluído.";
  ui.payStatus.textContent += " Recarregue a página para uma nova tentativa.";
  renderPay(outcome);
});

// Restaura sessão Pera existente (não assina nada).
pera
  .reconnectSession()
  .then((accounts) => {
    if (accounts.length) {
      pera.connector?.on("disconnect", () => setAccount(""));
      setAccount(accounts[0]!);
    } else setAccount("");
  })
  .catch(() => setAccount(""));
