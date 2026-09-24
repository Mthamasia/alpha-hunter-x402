import { AlgorandClient } from "@algorandfoundation/algokit-utils/algorand-client";
import { ExactAvmScheme } from "@x402/avm/exact/client";
import { x402Client, x402HTTPClient } from "@x402/core/client";
import type { SchemeNetworkClient } from "@x402/core/types";

import { EXPECTED, VALIDITY_WINDOW_ROUNDS } from "./config";
import { createPeraSigner, type PeraLike } from "./peraSigner";

/**
 * x402Client registrado SOMENTE para Algorand Mainnet, com spend controls
 * nativos da biblioteca limitando a 50000 unidades do ASA 31566704 (segunda
 * trava, além de validatePaymentRequired e assertExpectedPaymentTxn).
 */
export function buildHttpClient(scheme: SchemeNetworkClient): x402HTTPClient {
  const client = new x402Client()
    .register(EXPECTED.network, scheme)
    .setSpendControls({
      maxAmountPerPayment: "$0.05",
      allowedAssets: [
        { network: EXPECTED.network, asset: EXPECTED.asset, maxAmountPerPayment: EXPECTED.amount },
      ],
    });
  return new x402HTTPClient(client);
}

/**
 * AlgorandClient TestNet com janela de validade adequada à aprovação humana.
 * Sem isto o ExactAvmScheme cria AlgorandClient.testNet() com o padrão de 10 rounds.
 */
export function createMainnetAlgorand(): AlgorandClient {
  return AlgorandClient.mainNet().setDefaultValidityWindow(VALIDITY_WINDOW_ROUNDS);
}

/** Cliente x402 cujo signer é a Pera Wallet (um uso por instância). */
export function buildPeraHttpClient(
  pera: PeraLike,
  account: string,
  onBeforeSign?: () => void,
  algorand: AlgorandClient = createMainnetAlgorand(),
) {
  if (account !== EXPECTED.payer) throw new Error("A carteira conectada não é o payer Mainnet autorizado.");
  const signer = createPeraSigner(pera, account, { getLastRound: () => algorand.network.getLastRound() }, onBeforeSign);
  return buildHttpClient(new ExactAvmScheme(signer, { algorandClient: algorand }));
}
