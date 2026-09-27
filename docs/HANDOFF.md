# HANDOFF — leia antes de editar, atualize antes de sair

**Última atualização:** 26/09/2026, 22h, Claude Code.
**Trava:** `python scripts/agent_lock.py status`. Só edite com a trava em seu nome (regra no `AGENTS.md`).

## Atenção: outro processo escreveu nesta pasta com a trava ocupada

Em 26/09, com a trava em nome de `claude-code`, outro processo reescreveu `veratus_agents/data_discovery.py` (20:39) e `veratus_agents/catalog.py` (20:55). As duas versões são **antigas** e não correspondem a nenhum commit: removem o suporte à coleção feminina e a projeção pública. Com elas, `pytest` falha e `/os/readiness` dá 500. Esses arquivos **não foram revertidos** (regra do `AGENTS.md`). A rodada seguiu numa worktree isolada. Antes de qualquer commit na pasta principal, descubra quem escreveu e decida o que fazer com essas alterações.

Outras alterações não versionadas na pasta principal que **não** entraram em commit:

| Arquivo | Situação |
| --- | --- |
| `veratus_agents/data_discovery.py`, `veratus_agents/catalog.py` | versões antigas escritas por outro processo (acima) |
| `veratus_agents/product_master.py` | agente externo em 25/09: exige material também para joias (decisão 3 abaixo) |
| `integrations/zapier_to_sheets.md` | versão antiga que manda colocar a URL do Zapier no HTML público. Não usar |
| `social/fila-publicacao.csv` | formato alterado, origem desconhecida |
| `docs/data-discovery-2026-09-19.json` | contém nome e endereço do fornecedor. **Não versionar;** apagar ou guardar fora do repositório |
| `docs/evidence/*`, `docs/insights/*` (originais) | citam a cidade de origem; as cópias versionadas na branch estão redigidas |

## Estado do repositório

- `origin/main` = `38f50be`. Nada foi enviado nem publicado.
- `main` local: +6 commits (rodadas de 25/09 e 26/09 organizadas por tema).
- Branch `claude/rodada-2609` (a partir do `main` local): +12 commits desta rodada. Relatório em `docs/evidence/founder-round-2026-09-26.md`.
- Flags: `PUBLISH_ENABLED=false`, `PAID_MEDIA_LIVE_WRITES=false`, `PAID_MEDIA_AUTONOMY_MODE=SHADOW`, `WHATSAPP_SEND_ENABLED=false`, `INSTAGRAM_DM_ENABLED=false`, as 8 flags de canal `false`.
- Produção (`https://veratus.onrender.com`) ainda roda `38f50be`: `/os/paid-media/status` e `/integrations/whatsapp/webhook` respondem 404.

## Bloqueio principal

As imagens dos relógios mostram marca de terceiro no mostrador: ROLEX em 8 das 9, e o desenho de GMT no Royal Blue. Preço público, botão de pedido, Black GMT na vitrine e mídia paga ficam **suspensos** até as imagens serem trocadas por fotos do item exato sem marca de terceiros e o fundador confirmar que as peças não levam marca de terceiros (`STOREFRONT_BLOCKERS` em `commercial_config.py`). Os 8 relógios já publicados continuam com essas imagens.

## Verificar

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

- Testes de navegador (`tests/test_landing_browser.py`) usam o Chrome local e são pulados no CI.
- `tests/test_multiworker_state.py` sobe 2 processos reais. Com `VERATUS_TEST_DATABASE_URL` (um PostgreSQL descartável), roda também no PostgreSQL; os testes apagam as tabelas de snapshot desse banco.

## Decisões abertas do fundador

1. **Marca de terceiro nas imagens dos relógios** (bloqueio acima).
2. Push e deploy: `main` (+6) e `claude/rodada-2609` (+12).
3. Origem registrada com manuseio de 2 dias úteis vs entrega em até 7 dias. Quando o prazo começa a contar?
4. `product_master.py` (material obrigatório para joias no QA): manter ou reverter.
5. Economics: frete pago pela Veratus, taxa de pagamento e tributo (UNVERIFIED). Custo de devolução (desistência em 7 dias com frete grátis) não está modelado.
6. Preços das joias: nenhuma fonte real encontrada (9 × NEEDS_PRICING). Para publicar, preencher `sale_price` e `price_source`.
7. Fornecedor no histórico do Git (`commercial_config.py` até `38f50be`): reescrever ou não o histórico.
8. `VERATUS_SELLER_DOCUMENT` e `INSTAGRAM_ACCESS_TOKEN` no painel do Render (a chave de cifra `VERATUS_TOKEN_ENCRYPTION_KEY` é obrigatória para renovar o token).
9. Número do WhatsApp, ADR de tracking, app do Mercado Livre e 14º papel (`paid-acquisition-worker`), sem mudança desde 25/09.

## Não faça

- Não ligue nenhuma flag de envio ou publicação nem faça deploy sem confirmação explícita no momento da ação.
- Não publique preço ou anúncio de relógio enquanto o bloqueio de marca valer.
- Não "restaure" arquivos que você não alterou; registre aqui.
- Não versione `docs/data-discovery-2026-09-19.json` nem nada com nome, endereço ou cidade do fornecedor.

## Próximo passo sugerido

1. Resolver as alterações de outro processo na pasta principal (seção "Atenção").
2. Com o push aprovado: `git push origin main` e a branch; revisar e mesclar `claude/rodada-2609`.
3. Com o deploy aprovado: conferir `/health`, `/robots.txt`, `/sitemap.xml`, `/os/paid-media/status` (401 sem token) e `/integrations/whatsapp/webhook` (403 sem verify token). Depois, rodar `VERATUS_LANDING_URL=https://veratus.onrender.com pytest tests/test_landing_browser.py`.
