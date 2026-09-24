# integration-client-mainnet — pagamento x402 manual via Pera Wallet (Algorand Mainnet)

Cliente Mainnet isolado para executar no máximo **uma** compra x402 V2 de **0.05 USDC Mainnet** contra `POST /v1/token/investigate`. A Pera Wallet assina a transação: a chave privada nunca sai da carteira, e esta página nunca pede seed phrase, mnemonic ou private key.

## Stack (versões verificadas)

| Pacote | Versão | Uso |
|---|---|---|
| `@x402/core` | 2.27.0 | `x402Client`, `x402HTTPClient`, decode/encode dos headers V2, spend controls |
| `@x402/avm` | 2.27.0 | `ExactAvmScheme` (cliente): monta o grupo `[fee payer, axfer USDC]` |
| `@perawallet/connect` | 1.6.1 | `PeraWalletConnect({ chainId: 416001 })` (Mainnet) |
| `algosdk` | 3.8.0 | decode e checagem das transações antes de enviar à Pera |
| Vite / TypeScript / Vitest | 8 / 7 / 5 | build, dev server com proxy, testes |

`ExactAvmScheme` aceita qualquer `ClientAvmSigner = { address, signTransactions(txns, indexesToSign) }`. O arquivo [src/peraSigner.ts](src/peraSigner.ts) implementa essa interface delegando a assinatura a `PeraWalletConnect.signTransaction` e enviando `signers: []` para a transação do fee payer, que é assinada pelo facilitador.

## Fluxo

1. **Conectar Pera Wallet.** A sessão fica travada em Mainnet (chainId 416001) e aceita somente o payer esperado.
2. **Solicitar cotação.** A página faz um `POST` **sem pagamento**, recebe o 402 e decodifica o `PAYMENT-REQUIRED` com o decoder oficial.
3. A página mostra Network, Asset ID, Amount atomic, Amount USDC, payTo e Payer, e valida tudo contra [src/config.ts](src/config.ts):
   `algorand:wGHE2Pwdvd7S12BL5FaOP20EGYesN73ktiC1qzkkit8=` / `31566704` / `50000` /
   `ROO3X2KFNJVXIT2ZA6MFYOV7FRRZNVH2WIPO6TDWFVNEHVMQGFT6O4KFLA`.
   Qualquer divergência, ausência de Bazaar/tag, payer inesperado ou mais de uma opção bloqueia o pagamento.
4. **PAY 0.05 USDC MAINNET.** Esse botão só é habilitado depois da validação. O clique toma a trava da página **antes** de qualquer `await`.
5. O adaptador aceita somente o grupo `[fee payer, axfer USDC]` com Mainnet, ASA, amount, payer e receiver exatos, sem rekey/close/clawback.

Travas contra cobrança dupla:
- uma tentativa por carregamento da página;
- a sessão de pagamento vale para uma única tentativa;
- o signer é de uso único;
- não há retry automático nem timers.

Os spend controls do `x402Client` limitam o ASA 31566704 a 50000 unidades como segunda barreira.

## Como rodar

```bash
# cliente (o proxy /api usa o Railway público por padrão)
cd integration-client-mainnet
npm install
npm run dev          # http://127.0.0.1:5174
```

O proxy do Vite deixa API e página na mesma origem, então os headers `PAYMENT-*` ficam legíveis. O destino padrão é `https://alpha-hunter-x402-production.up.railway.app`; para desenvolvimento local, defina `AHX_API_URL`.

Na Pera Wallet (celular), selecione explicitamente **Algorand Mainnet** antes de conectar.

## Testes e build

```bash
npm test         # vitest: parsing 402, validação, adaptador Pera, fluxo, travas, segurança
npm run build    # tsc --noEmit + vite build
```

Os testes não acessam rede (`fetch` global é bloqueado) e não movimentam fundos: usam uma Pera falsa, um scheme falso e transações montadas offline com assinaturas zeradas.
