/**
 * Valores esperados para a ÚNICA compra de teste. Qualquer divergência no 402
 * recebido do servidor bloqueia o pagamento antes de qualquer assinatura.
 *
 * Fonte de verdade do payTo: PAY_TO confirmado na Fase 2 do resource server.
 */
export const EXPECTED = {
  x402Version: 2,
  scheme: "exact",
  // CAIP-2 da Algorand Testnet (genesis hash testnet-v1.0)
  network: "algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI=",
  genesisHash: "SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI=",
  genesisId: "testnet-v1.0",
  asset: "10458941", // USDC Testnet
  amount: "50000", // 0.05 USDC (6 decimais)
  decimals: 6,
  payTo: "A5D55SSWZFKTMSZ2CCCOMDVM3J4ROZT2Z2KAKXM26WQEVSLVSM3ZEEPKMQ",
} as const;

/** Genesis hash da Mainnet: nunca aceito. */
export const MAINNET_GENESIS_HASH = "wGHE2Pwdvd7S12BL5FaOP20EGYesN73ktiC1qzkkit8=";

/** Pera Connect: 416001 = MainNet, 416002 = TestNet. */
export const PERA_TESTNET_CHAIN_ID = 416002 as const;

/** Caminho via proxy do Vite (mesma origem) para o resource server local. */
export const INVESTIGATE_PATH = "/api/v1/token/investigate";

export const PAY_BUTTON_LABEL = "PAY 0.05 USDC TESTNET";

/**
 * Janela de validade do grupo (lastValid - firstValid), em rounds.
 *
 * O TransactionComposer da algokit-utils usa 10 rounds por padrão (~28 s na
 * TestNet), curto demais para aprovação humana na Pera + verify + settle ->
 * "txn dead". 100 rounds (~280 s a ~2.8 s/round) cabe dentro do
 * maxTimeoutSeconds=300 anunciado pelo servidor e bem abaixo do máximo do
 * protocolo (1000).
 */
export const VALIDITY_WINDOW_ROUNDS = 100;

/** Antes de abrir a Pera: o grupo precisa ter pelo menos isto de validade restante. */
export const MIN_ROUNDS_BEFORE_SIGN = 60; // ~2.8 min para o usuário aprovar

/** Depois da assinatura: margem mínima para verify + simulate + settle no facilitador. */
export const MIN_ROUNDS_AFTER_SIGN = 10; // ~28 s
