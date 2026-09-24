# ahx-x402-api

MVP de uma API comercial de inteligência on-chain (Solana). O objetivo é consumir, no futuro, resultados do **Alpha Hunter X** e cobrar por requisição via **x402** (HTTP 402 Payment Required).

> **Estado atual:** nenhuma fonte de inteligência está conectada. A API responde `status: "NO_DATA_SOURCE"` e **não fabrica dados**. O pagamento tem três modos: `disabled`, `test` (simulado) e `x402-testnet` (x402 V2 real na **Algorand Testnet**, via biblioteca oficial `x402-avm` e o facilitador GoPlausible). Mainnet é bloqueada na configuração.

## Arquitetura

```
app/
  main.py              # FastAPI: rotas, middleware (request_id, limite de body), handlers de erro
  config.py            # Settings a partir do ambiente/.env (valida PAYMENT_MODE, bloqueia mainnet)
  models.py            # Schemas Pydantic (request/response v1.0) + validação base58 do mint
  providers/
    base.py            # IntelligenceProvider (interface) + ProviderResult
    stub.py            # StubIntelligenceProvider -> sempre NO_DATA_SOURCE
  payments/
    gate.py            # PaymentGate (disabled | test), header X-PAYMENT simulado
    x402_avm.py        # x402-testnet: middleware oficial x402 + esquema exact AVM
tests/                 # pytest; rede externa bloqueada por fixture autouse
```

Em `x402-testnet`, a middleware oficial `x402.http.middleware.fastapi.payment_middleware` intercepta `POST /v1/token/investigate` **antes** do endpoint. O projeto não implementa protocolo próprio. Só monta a rota: esquema `exact`, rede, asset, amount, payTo e facilitador.

Fluxo de `POST /v1/token/investigate`:

1. O Pydantic valida o body (`chain == "solana"`, `mint` base58 que decodifica para 32 bytes, sem campos extras).
2. `PaymentGate.verify()` libera a requisição ou levanta `PaymentRequired` (HTTP 402).
3. `IntelligenceProvider.investigate(chain, mint)` retorna um `ProviderResult`.
4. A API monta a resposta versionada (`schema_version`, `request_id`, `generated_at`).

Como o provider e o gate são injetados em `create_app(settings, provider)`, um futuro `AlphaHunterProvider` pode ser plugado sem mudar o contrato público.

## Setup

Requer Python 3.11+.

```bash
python -m venv .venv
# Windows (PowerShell):  .venv\Scripts\Activate.ps1
# Windows (Git Bash):    source .venv/Scripts/activate
# Linux/macOS:           source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # opcional; os padrões já são seguros
```

### Variáveis (`.env.example`)

| Variável       | Padrão        | Descrição |
|----------------|---------------|-----------|
| `APP_ENV`      | `development` | Informativo (exposto em `/version`). |
| `PAYMENT_MODE` | `disabled`    | `disabled`, `test` ou `x402-testnet`. Qualquer outro valor impede a inicialização. |
| `PRICE_USD`    | `0.05`        | Preço por chamada. Em `x402-testnet` vira unidades mínimas de USDC (6 casas, via `Decimal`): `0.05` → `50000`. Mais de 6 casas decimais é rejeitado. |
| `PAY_TO`       | *(vazio)*     | Endereço Algorand de recebimento. **Obrigatório** e validado (checksum) em `x402-testnet`. Configure-o no `.env` local, que é ignorado pelo Git. |
| `X402_NETWORK` | `testnet`     | Em `x402-testnet` aceita `testnet`, `algorand-testnet` ou o CAIP-2 da Testnet. Valores contendo `mainnet`, ou qualquer outra rede, são rejeitados. |
| `X402_FACILITATOR_URL` | `https://facilitator.goplausible.xyz` | Facilitador x402. Precisa ser `https` e não pode conter credenciais. |

Nunca coloque seed phrase ou private key no `.env`; nenhuma variável deste projeto precisa disso.

## Testes

```bash
python -m pytest -v
```

Os testes não acessam rede externa: uma fixture autouse em `tests/conftest.py` bloqueia qualquer conexão ou DNS fora de loopback. O loopback continua liberado porque o asyncio no Windows usa um socketpair interno.

## Executar

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
# modo pagamento simulado:
PAYMENT_MODE=test uvicorn app.main:app --host 127.0.0.1 --port 8000     # bash
$env:PAYMENT_MODE="test"; uvicorn app.main:app --host 127.0.0.1 --port 8000  # PowerShell
```

## Exemplos curl

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/version

curl -X POST http://127.0.0.1:8000/v1/token/investigate \
  -H "Content-Type: application/json" \
  -d '{"chain":"solana","mint":"So11111111111111111111111111111111111111112"}'
```

Com `PAYMENT_MODE=test`, a chamada acima retorna **402** com os requisitos de pagamento (`accepts`). Para liberar, gere um header de teste:

```bash
PAY=$(python -c "from app.payments.gate import build_test_payment_header as b; print(b('testnet'))")
curl -i -X POST http://127.0.0.1:8000/v1/token/investigate \
  -H "Content-Type: application/json" -H "X-PAYMENT: $PAY" \
  -d '{"chain":"solana","mint":"So11111111111111111111111111111111111111112"}'
```

O header é base64 de:
`{"x402Version":1,"scheme":"test","network":"<X402_NETWORK>","payload":{"test":true}}`.
A resposta 200 traz `X-PAYMENT-RESPONSE` (base64 de `{"success":true,"simulated":true,...}`).

### Resposta (stub)

```json
{
  "schema_version": "1.0",
  "request_id": "…uuid…",
  "chain": "solana",
  "mint": "So11111111111111111111111111111111111111112",
  "status": "NO_DATA_SOURCE",
  "generated_at": "2026-01-01T00:00:00.000000Z",
  "intelligence": {
    "creator": null, "wallet_activity": [], "concentration": null,
    "behavioral_findings": [], "evidence": []
  },
  "data_quality": {
    "status": "UNAVAILABLE",
    "limitations": ["Nenhuma fonte de inteligência está conectada (provider=stub).", "…"]
  }
}
```

Os erros seguem um envelope único: `{"error": {"code", "message", "request_id", ...}}` (422 validação, 413 body > 4 KB, 404, 500). A exceção é o 402, que usa o formato estilo x402 (`x402Version`, `accepts`, `simulated`).

## x402 real — `PAYMENT_MODE=x402-testnet` (Algorand Testnet)

```bash
# .env local (NÃO versionado)
PAYMENT_MODE=x402-testnet
PAY_TO=<seu endereço Algorand Testnet>
X402_NETWORK=testnet
X402_FACILITATOR_URL=https://facilitator.goplausible.xyz
PRICE_USD=0.05
```

Fluxo (x402 V2):

1. Sem pagamento, `POST /v1/token/investigate` retorna **402** com corpo `{}` e o header `PAYMENT-REQUIRED` (base64 do `PaymentRequired`). Exemplo decodificado:
   ```json
   {"x402Version": 2, "accepts": [{
     "scheme": "exact",
     "network": "algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI=",
     "asset": "10458941", "amount": "50000", "payTo": "<PAY_TO>",
     "maxTimeoutSeconds": 300,
     "extra": {"decimals": 6, "feePayer": "<do facilitador>", "genesisId": "testnet-v1.0", "genesisHash": "…"}}]}
   ```
2. O cliente pagador (fora deste repositório) assina a transação USDC e reenvia a requisição com o header `PAYMENT-SIGNATURE`.
3. A middleware chama o **verify** do facilitador. Se o pagamento for válido, executa o endpoint. Se o endpoint responder < 400, chama o **settle** e devolve 200 com o header `PAYMENT-RESPONSE`.
4. Uma resposta de erro do endpoint (por exemplo, 422) **não** é liquidada. Uma falha no settle retorna 402 e não entrega o recurso.
5. Se o facilitador estiver indisponível ou não suportar a rede, a API **falha fechada** com `503 payment_unavailable`.

Decodificando o header:

```bash
curl -s -D - -o /dev/null -X POST http://127.0.0.1:8000/v1/token/investigate \
  -H "Content-Type: application/json" \
  -d '{"chain":"solana","mint":"So11111111111111111111111111111111111111112"}' \
  | grep -i '^payment-required:' | cut -d' ' -f2 | tr -d '\r' \
  | python -c "import sys;from x402.http import decode_payment_required_header as d;print(d(sys.stdin.read().strip()).model_dump_json(by_alias=True,indent=2))"
```

O servidor **não possui chave privada**: quem assina é o pagador e quem submete a transação é o facilitador. Nenhuma variável do projeto guarda segredo.

### Cliente pagador (Pera Wallet)

A pasta [integration-client/](integration-client/) contém um cliente de navegador para fazer o primeiro pagamento real em TestNet usando a Pera Wallet como signer. Ele valida o 402 antes de assinar e só paga depois de um clique explícito. Veja o [README do cliente](integration-client/README.md).

## Payment `test` vs x402 real

| | `PAYMENT_MODE=test` | `PAYMENT_MODE=x402-testnet` |
|---|---|---|
| Resposta 402 | Estilo x402, corpo JSON com `simulated: true` | x402 V2 oficial: header `PAYMENT-REQUIRED` |
| Prova de pagamento | `X-PAYMENT`: JSON base64 fixo com `scheme: "test"` | `PAYMENT-SIGNATURE`: grupo de transações assinado pela carteira do pagador |
| Verificação | Checagem de formato local | verify + settle via facilitador GoPlausible |
| Blockchain, fundos | **Nenhum** | USDC Testnet (ASA 10458941), sem valor real |
| Replay protection | **Nenhuma** | Garantida on-chain: cada transação só é liquidada uma vez |

O modo `test` serve apenas para exercitar o contrato HTTP 402 com clientes. **Ele não oferece nenhuma segurança de cobrança.**

## Limitações atuais

- Nenhuma fonte de dados: toda investigação retorna `NO_DATA_SOURCE` com `intelligence` vazio.
- Apenas `chain = "solana"`. A validação do mint checa só o formato (base58, 32 bytes); não confirma que a conta existe on-chain.
- O modo `test` é simulado e não tem proteção contra replay.
- `x402-testnet` ainda **não foi exercitado com um pagamento real**: o fluxo verify/settle foi testado apenas com um facilitador falso. O primeiro pagamento real depende de um cliente pagador, fora do escopo deste repositório.
- Em `x402-testnet`, a middleware x402 roda antes da validação do body. Uma requisição sem pagamento recebe 402 mesmo com mint inválido. Com pagamento válido e mint inválido, a resposta é 422 e **nada é liquidado**.
- A primeira requisição paga busca `/supported` no facilitador de forma síncrona. Se falhar, a API responde 503 e tenta de novo na requisição seguinte.
- Requisições de navegador (`Accept: text/html`) recebem o paywall HTML da biblioteca, que na versão 2.0.2 usa o template EVM mesmo para Algorand.
- Em falha de settle, a biblioteca inclui no corpo 402 a mensagem de erro do facilitador.
- O limite de body (4 KB) vale só quando existe `Content-Length`. Corpos chunked grandes dependem dos limites de tamanho dos campos Pydantic.
- Não há rate limiting, autenticação, cache nem persistência (SQLite não foi necessário).

## Próximos passos

1. **Primeiro pagamento em Testnet** via [integration-client/](integration-client/) (Pera Wallet), após autorização explícita.
2. **AlphaHunterProvider**: implementar `IntelligenceProvider` consumindo um output exportado ou uma API do Alpha Hunter X (contrato a definir), mapeando para `Intelligence` e `evidence` com proveniência. Retornar `PARTIAL` e `limitations` quando faltarem dados.
3. Rate limiting e logs estruturados.
4. Mainnet apenas após revisão de segurança explícita.
