/**
 * Valores esperados para a ÚNICA compra Mainnet. Qualquer divergência no 402
 * recebido do servidor bloqueia o pagamento antes de qualquer assinatura.
 *
 * Fonte de verdade: desafio Mainnet validado antes de qualquer assinatura.
 */
export const EXPECTED = {
  x402Version: 2,
  scheme: "exact",
  // CAIP-2 da Algorand Mainnet (genesis hash mainnet-v1.0)
  network: "algorand:wGHE2Pwdvd7S12BL5FaOP20EGYesN73ktiC1qzkkit8=",
  genesisHash: "wGHE2Pwdvd7S12BL5FaOP20EGYesN73ktiC1qzkkit8=",
  genesisId: "mainnet-v1.0",
  asset: "31566704", // USDC Mainnet
  amount: "50000", // 0.05 USDC (6 decimais)
  decimals: 6,
  payTo: "ROO3X2KFNJVXIT2ZA6MFYOV7FRRZNVH2WIPO6TDWFVNEHVMQGFT6O4KFLA",
  payer: "3KSUELDHDB73AVCRNQSJD27HVCAIET4LT34FPM6L25IQ34IMZMJM2D4V7E",
  feePayer: "ZMFK2OI7ZBD2U27ISERZC4S6LKM6WMFJPZQ4MYNJDZ2VNBNMBA67RA22AA",
} as const;

/** Genesis hash da Testnet: nunca aceito. */
export const TESTNET_GENESIS_HASH = "SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI=";

/** Pera Connect: 416001 = MainNet. */
export const PERA_MAINNET_CHAIN_ID = 416001 as const;

/** Caminho via proxy do Vite para o resource server Mainnet público. */
export const INVESTIGATE_PATH = "/api/v1/token/investigate";

export const PAY_BUTTON_LABEL = "PAY 0.05 USDC MAINNET";

/**
 * Janela de validade do grupo (lastValid - firstValid), em rounds.
 *
 * O TransactionComposer da algokit-utils usa 10 rounds por padrão (~28 s na
 * Mainnet), curto demais para aprovação humana na Pera + verify + settle ->
 * "txn dead". 100 rounds (~280 s a ~2.8 s/round) cabe dentro do
 * maxTimeoutSeconds=300 anunciado pelo servidor e bem abaixo do máximo do
 * protocolo (1000).
 */
export const VALIDITY_WINDOW_ROUNDS = 100;

/** Antes de abrir a Pera: o grupo precisa ter pelo menos isto de validade restante. */
export const MIN_ROUNDS_BEFORE_SIGN = 60; // ~2.8 min para o usuário aprovar

/** Depois da assinatura: margem mínima para verify + simulate + settle no facilitador. */
export const MIN_ROUNDS_AFTER_SIGN = 10; // ~28 s
