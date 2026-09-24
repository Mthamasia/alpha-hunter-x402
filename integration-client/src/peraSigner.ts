/**
 * Adaptador Pera Wallet -> ClientAvmSigner (@x402/avm).
 *
 * A chave privada nunca sai da Pera Wallet: este módulo só recebe transações
 * não assinadas, mostra-as à Pera e recebe de volta as assinadas.
 *
 * Antes de enviar à Pera, confere de forma independente que a transação a
 * assinar é exatamente a transferência esperada (Testnet, ASA, amount, payTo).
 */
import type { ClientAvmSigner } from "@x402/avm";
import algosdk from "algosdk";

import { EXPECTED, MIN_ROUNDS_AFTER_SIGN, MIN_ROUNDS_BEFORE_SIGN, VALIDITY_WINDOW_ROUNDS } from "./config";

export interface RoundGuard {
  /** Round atual da TestNet (algorand.network.getLastRound()). */
  getLastRound: () => Promise<bigint>;
}

/**
 * Garante que o grupo ainda terá validade suficiente. Sem isso, um grupo
 * assinado tarde demais é recusado pelo facilitador com "txn dead".
 */
export function assertValidityWindow(
  txns: algosdk.Transaction[],
  lastRound: bigint,
  minRemaining: number,
  stage: "antes da assinatura" | "após a assinatura",
): void {
  for (const txn of txns) {
    const window = txn.lastValid - txn.firstValid;
    if (window <= 0n || window > BigInt(VALIDITY_WINDOW_ROUNDS)) {
      throw new Error(`Janela de validade inesperada (${window} rounds); esperado até ${VALIDITY_WINDOW_ROUNDS}.`);
    }
    const remaining = txn.lastValid - lastRound;
    if (remaining < BigInt(minRemaining)) {
      throw new Error(
        `Grupo expirado ou perto de expirar ${stage}: round atual ${lastRound}, ` +
          `lastValid ${txn.lastValid} (restam ${remaining} < ${minRemaining} rounds). ` +
          "Nada foi enviado; recarregue a página para uma nova tentativa.",
      );
    }
  }
}

/** Subconjunto da API real de PeraWalletConnect usado aqui (facilita testes). */
export interface PeraLike {
  signTransaction(
    txGroups: { txn: algosdk.Transaction; signers?: string[] }[][],
    signerAddress?: string,
  ): Promise<Uint8Array[]>;
}

function b64(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s);
}

/** Checagem independente da transação que o usuário vai assinar. */
export function assertExpectedPaymentTxn(txn: algosdk.Transaction, payer: string): void {
  const problems: string[] = [];
  if (txn.type !== algosdk.TransactionType.axfer) problems.push(`tipo ${txn.type} ≠ axfer`);
  if (txn.sender.toString() !== payer) problems.push("sender ≠ carteira conectada");
  if (txn.genesisID !== EXPECTED.genesisId) problems.push(`genesisID ${txn.genesisID} não é Testnet`);
  if (!txn.genesisHash || b64(txn.genesisHash) !== EXPECTED.genesisHash) {
    problems.push("genesisHash não é Testnet");
  }
  const axfer = txn.assetTransfer;
  if (!axfer) {
    problems.push("sem campos de asset transfer");
  } else {
    if (axfer.assetIndex !== BigInt(EXPECTED.asset)) problems.push(`ASA ${axfer.assetIndex} ≠ ${EXPECTED.asset}`);
    if (axfer.amount !== BigInt(EXPECTED.amount)) problems.push(`amount ${axfer.amount} ≠ ${EXPECTED.amount}`);
    if (axfer.receiver.toString() !== EXPECTED.payTo) problems.push("receiver ≠ payTo esperado");
    if (axfer.closeRemainderTo) problems.push("closeRemainderTo presente (bloqueado)");
    if (axfer.assetSender) problems.push("clawback/assetSender presente (bloqueado)");
  }
  if (txn.rekeyTo) problems.push("rekeyTo presente (bloqueado)");
  if (problems.length) throw new Error(`Transação recusada antes da assinatura: ${problems.join("; ")}`);
}

/**
 * Cria um ClientAvmSigner que delega a assinatura à Pera Wallet.
 * `onBeforeSign` é chamado imediatamente antes de abrir a Pera (para a UI).
 */
export function createPeraSigner(
  pera: PeraLike,
  address: string,
  guard: RoundGuard,
  onBeforeSign?: () => void,
): ClientAvmSigner {
  if (!algosdk.isValidAddress(address)) throw new Error("Endereço Pera inválido.");
  let used = false;

  return {
    address,
    async signTransactions(txns: Uint8Array[], indexesToSign?: number[]) {
      // Uma assinatura por signer: impede qualquer segunda cobrança com o mesmo objeto.
      if (used) throw new Error("Este signer já foi usado; nova cotação e novo clique são necessários.");
      used = true;

      const decoded = txns.map((t) => algosdk.decodeUnsignedTransaction(t));
      const toSign = indexesToSign ?? decoded.map((_, i) => i);
      if (toSign.length !== 1) throw new Error(`Esperada 1 transação para assinar, recebidas ${toSign.length}.`);

      for (const i of toSign) {
        const txn = decoded[i];
        if (!txn) throw new Error(`Índice ${i} fora do grupo.`);
        assertExpectedPaymentTxn(txn, address);
      }
      for (const txn of decoded) {
        if (txn.genesisID !== EXPECTED.genesisId) throw new Error("Grupo contém transação fora da Testnet.");
      }

      // Grupo acabou de ser montado com params frescos; confirma que ainda está longe de expirar.
      assertValidityWindow(decoded, await guard.getLastRound(), MIN_ROUNDS_BEFORE_SIGN, "antes da assinatura");

      const group = decoded.map((txn, i) =>
        toSign.includes(i) ? { txn, signers: [address] } : { txn, signers: [] as string[] },
      );

      onBeforeSign?.();
      const signed = await pera.signTransaction([group], address);

      // A aprovação humana pode demorar: não envia um grupo que o facilitador rejeitaria.
      assertValidityWindow(decoded, await guard.getLastRound(), MIN_ROUNDS_AFTER_SIGN, "após a assinatura");

      // A Pera devolve apenas as transações assinadas, em ordem.
      if (signed.length !== toSign.length) {
        throw new Error(`Pera retornou ${signed.length} assinaturas, esperadas ${toSign.length}.`);
      }
      const out: (Uint8Array | null)[] = decoded.map(() => null);
      toSign.forEach((idx, k) => {
        const blob = signed[k]!;
        const stxn = algosdk.decodeSignedTransaction(blob);
        if (stxn.txn.txID() !== decoded[idx]!.txID()) {
          throw new Error("Transação assinada pela Pera difere da transação solicitada.");
        }
        out[idx] = blob;
      });
      return out;
    },
  };
}
