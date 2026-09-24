# integration-client — pagamento x402 manual via Pera Wallet (Algorand TestNet)

Cliente de navegador para executar **uma** compra x402 V2 real de **0.05 USDC TestNet** contra `POST /v1/token/investigate`. A Pera Wallet assina a transação: a chave privada nunca sai da carteira, e esta página nunca pede seed phrase, mnemonic ou private key.

## Stack (versões verificadas)

| Pacote | Versão | Uso |
|---|---|---|
| `@x402/core` | 2.27.0 | `x402Client`, `x402HTTPClient`, decode/encode dos headers V2, spend controls |
| `@x402/avm` | 2.27.0 | `ExactAvmScheme` (cliente): monta o grupo `[fee payer, axfer USDC]` |
| `@perawallet/connect` | 1.6.1 | `PeraWalletConnect({ chainId: 416002 })` (TestNet) |
| `algosdk` | 3.8.0 | decode e checagem das transações antes de enviar à Pera |
| Vite / TypeScript / Vitest | 8 / 7 / 5 | build, dev server com proxy, testes |

`ExactAvmScheme` aceita qualquer `ClientAvmSigner = { address, signTransactions(txns, indexesToSign) }`. O arquivo [src/peraSigner.ts](src/peraSigner.ts) implementa essa interface delegando a assinatura a `PeraWalletConnect.signTransaction` e enviando `signers: []` para a transação do fee payer, que é assinada pelo facilitador.

## Fluxo

1. **Conectar Pera Wallet.** A sessão fica travada em TestNet (chainId 416002) e a página mostra o endereço conectado.
2. **Solicitar cotação.** A página faz um `POST` **sem pagamento**, recebe o 402 e decodifica o `PAYMENT-REQUIRED` com o decoder oficial.
3. A página mostra Network, Asset ID, Amount atomic, Amount USDC, payTo e Payer, e valida tudo contra [src/config.ts](src/config.ts):
   `algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI=` / `10458941` / `50000` / `A5D55SSWZFKTMSZ2CCCOMDVM3J4ROZT2Z2KAKXM26WQEVSLVSM3ZEEPKMQ`.
   Qualquer divergência, ou mais de uma opção em `accepts`, bloqueia o pagamento.
4. **PAY 0.05 USDC TESTNET.** Esse botão só é habilitado depois da validação. O clique toma a trava da página **antes** de qualquer `await`.
5. O `ExactAvmScheme` monta o grupo e o adaptador confere de novo a transação a assinar (axfer, ASA, amount, receiver, Testnet, sem rekey/close). Só então abre a Pera para aprovação.
6. A requisição é reenviada **uma vez** com `PAYMENT-SIGNATURE`. A página mostra o HTTP final, o resultado da API, o `PAYMENT-RESPONSE`, o transaction ID (com link para o explorer Lora TestNet) e o erro, se houver.

Travas contra cobrança dupla:
- uma tentativa por carregamento da página;
- a sessão de pagamento vale para uma única tentativa;
- o signer é de uso único;
- não há retry automático nem timers.

Os spend controls do `x402Client` limitam o ASA 10458941 a 50000 unidades como segunda barreira.

## Como rodar

```bash
# 1) resource server (na raiz do repositório), modo x402-testnet
PAYMENT_MODE=x402-testnet PAY_TO=A5D55SSWZFKTMSZ2CCCOMDVM3J4ROZT2Z2KAKXM26WQEVSLVSM3ZEEPKMQ \
  .venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 2) cliente
cd integration-client
npm install
npm run dev          # http://127.0.0.1:5173  (proxy /api -> http://127.0.0.1:8000)
```

O proxy do Vite deixa API e página na mesma origem, então o servidor não precisa de CORS e os headers `PAYMENT-*` ficam legíveis. Para usar outra porta ou host na API, defina `AHX_API_URL`.

Na Pera Wallet (celular), ative **Developer Settings → Node Settings → TestNet** antes de conectar.

## Testes e build

```bash
npm test         # vitest: parsing 402, validação, adaptador Pera, fluxo, travas, segurança
npm run build    # tsc --noEmit + vite build
```

Os testes não acessam rede (`fetch` global é bloqueado) e não movimentam fundos: usam uma Pera falsa, um scheme falso e transações montadas offline com assinaturas zeradas.
