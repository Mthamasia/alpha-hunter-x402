# Relatório de prontidão comercial — 2026-10-15

Veredito: **NO-GO** para venda. Nenhuma cobrança real foi feita nem habilitada por este trabalho.

## Legenda
- **FATO** — observado diretamente (comando, resposta HTTP, código, registro do GitHub).
- **INFERÊNCIA** — dedução a partir de fatos, não verificada diretamente.
- **NÃO VERIFICADO** — não foi possível observar a partir deste ambiente.

## 1. Inventário

| Item | Estado | Base |
|---|---|---|
| API pública Railway | no ar; `/health` ok, `/ready` provider `alpha_hunter`, `/version` `x402-mainnet`, `app_env=production` | FATO (GET em 2026-10-15) |
| Commit em produção | `4df5dbe` (deployment GitHub `success`, 2026-10-12) | FATO (API de deployments + OpenAPI ao vivo sem `/v1/token/preview`) |
| PR #6 (preview/telemetria) | aberto, não implantado, CI verde | FATO |
| PR #7 (gate de evidência) | draft, não implantado, CI verde | FATO |
| Código da ponte `app/x402_bridge` | **não está no GitHub** (só na VPS) | FATO (repo `alpha-hunter-x`, só `main`, sem o módulo) |
| Coletor AHX na VPS | `inactive` em 2026-10-12 | FATO documentado em `docs/vps-bridge-read-only-audit.md` (PR #6); estado de hoje NÃO VERIFICADO |
| Ponte / Railway / VPS a partir daqui | inacessíveis (proxy nega CONNECT; sem token da ponte) | FATO |

## 2. O que estava quebrado

1. **Gate de cobrança em produção (`4df5dbe`) aceita evidência não verificável.** Basta `evidence` não vazio e qualquer campo de inteligência não vazio. O formato de ponte registrado nos testes (`{"source": "read-only-bridge"}` + `trust_score: 100` sem lançamentos observados) seria cobrado se rotulado FRESH. (FATO no código; ocorrência real NÃO VERIFICADA.)
2. **"FRESH" vinha só do rótulo da ponte**, sem cronologia independente. (FATO)
3. **Nenhuma checagem de associação evidência↔mint.** (FATO)
4. **Reputação sem resultados observados era repassada como número.** (FATO)
5. **Marketcap anômalo/contraditório passava sem sinalização.** (FATO)
6. **PR #6 e #7 conflitavam** em `app/main.py`. (FATO)
7. **Erros da ponte não entravam na telemetria**; sem latência registrada; sem relatório de operador. (FATO)
8. **`/version` não identifica o commit implantado.** (FATO)
9. **Ponte não versionada.** Risco de engenharia: o contrato de dados vendido não tem fonte auditável. (FATO)

## 3. O que foi corrigido (branch `integration/commercial-readiness`)

- Merge de #7 e #6 com conflito resolvido mantendo ambos os comportamentos.
- `app/quality.py`: elegibilidade de venda exige assinatura Solana válida (64 bytes base58) ou URL de explorer com assinatura, `mint` igual ao solicitado, `block_time` recente pelo relógio do servidor (padrão 24 h, `PAID_EVIDENCE_MAX_AGE_SECONDS`) e achado observado. Endurece o PR #7: `event_id`, `slot` e "reference" livre deixam de bastar.
- Sanitização pública: chaves sensíveis removidas; reputação sem desfechos observados → `UNKNOWN`; marketcap anômalo ou contraditório (>10×) removido e listado em `limitations`.
- Preview gratuito retorna `premium_eligible` e códigos de motivo (mesmo gate). O 503 sem cobrança também retorna os códigos.
- Telemetria de erros da ponte (`served_from=error`), `latency_ms` no log, `scripts/telemetry_report.py` somente leitura.
- `/version` expõe `commit` (`RAILWAY_GIT_COMMIT_SHA`).
- Documentos: `agent-integration.md`, `bridge-evidence-contract.md`, este relatório.

## 4. Testes

**Executados aqui (offline):** 286 aprovados, 0 falhas. Base antes: main 97, PR #6 174, PR #7 165.
Dependências montadas a partir dos fontes no GitHub (PyPI inacessível no sandbox), incluindo `x402-avm` 2.0.2 (branch `branch-v2-algorand-publish`, versão idêntica à fixada) e `pynacl`/`pycryptodomex` compilados localmente. FastAPI 0.115.12 / Starlette 0.46.2. O CI do GitHub (Python 3.12, PyPI) é a verificação independente.

**Natureza dos testes:** app FastAPI, middleware x402-avm, mapeamento real do `AlphaHunterProvider`, gate, quota e telemetria são reais. **Respostas da ponte e o facilitador são simulados.** Nenhum teste usa dados reais da ponte. Cenários cobertos: mint válido completo; mint com dados antigos; rótulo STALE; sem evidência; formato atual da ponte; evidência de outro mint; mint inexistente (404); mint inválido; ponte fora (conexão, timeout, 500, JSON inválido, schema errado); mint divergente; reputação não observada; marketcap contraditório/anômalo; vazamento de campos internos; preview elegível/inelegível; abuso de quota e scraping de mints; consistência telemetria/receita; liquidação falha não vira receita.

**Testes reais executados:** apenas GETs públicos em produção (`/health`, `/ready`, `/version`, `/.well-known/x402`, `/openapi.json`). Nenhum POST, nenhum pagamento.

## 5. Exemplo comprovável de investigação premium

**Não existe.** O único pagamento real registrado (2026-09-24, tx Algorand `VZUFPPPV37C3CR45S3JGPHKENFX4GWZ6HWW5UDWM4WDVAI7O3COLA`) entregou wSOL com `status: STALE` e vários campos `null`. Pelo gate novo (e pelo próprio `4df5dbe`) essa resposta não seria cobrável. Não foi possível obter uma resposta real nova sem pagar e sem acesso à ponte.

## 6. Cobrança x402

- Produção aceita pagamentos reais em Algorand Mainnet USDC, US$ 0,05 (FATO via discovery/version).
- Risco residual em produção: cobrança por evidência não verificável se a ponte devolver FRESH (ver §2.1).
- Na branch: liquidação só ocorre com 200; todos os caminhos sem inteligência comprovável retornam 503 sem `PAYMENT-RESPONSE` e sem `settle` (testado com facilitador simulado).

## 7. Custos observados

- Helius pela API: **0 chamadas por construção** (a API não tem caminho para Helius; testes afirmam `helius_calls_total == 0`). A ponte não abre conexões externas (auditoria de 12/10).
- Custos reais (Railway, VPS, Helius do coletor): NÃO VERIFICADOS daqui.
- Este trabalho: zero chamadas Helius, zero pagamentos.

## 8. Veredito e próxima decisão

**NO-GO.** Motivos impeditivos:
1. Nenhuma resposta premium real que passe no gate foi demonstrada.
2. O formato conhecido da ponte não fornece evidência verificável, ligada ao mint e com timestamp.
3. Os dados provavelmente estão STALE (coletor inativo em 12/10).
4. A produção roda um gate que pode cobrar por evidência fraca.

**Única próxima decisão necessária:** autorizar a execução, na VPS, de uma auditoria somente leitura da ponte. Ela consiste em versionar `app/x402_bridge` no GitHub e capturar respostas reais para uma amostra fixa de mints, sem Helius e sem escrita. Essa execução exige acesso à VPS que este ambiente não tem. Com isso decidimos se o contrato de evidência (`bridge-evidence-contract.md`) é atendível a partir das tabelas existentes. Até lá, o recomendado é **não divulgar a API como pronta** e, por decisão sua, colocar a produção em `PAYMENT_MODE=disabled` ou implantar esta branch (que só cobra com evidência comprovável).
