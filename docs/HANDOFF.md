# HANDOFF — leia antes de editar, atualize antes de sair

**Última atualização:** 27/09/2026, Claude Code.
**Trava:** `python scripts/agent_lock.py status`. Só edite com a trava em seu nome (regra no `AGENTS.md`).

## Atenção: outro processo escreveu na pasta principal

Em 26/09, com a trava em nome de `claude-code`, outro processo reescreveu `veratus_agents/data_discovery.py` (20:39) e `veratus_agents/catalog.py` (20:55). As duas versões são **antigas** e não correspondem a nenhum commit: removem o suporte à coleção feminina e a projeção pública. Com elas, `pytest` falha e `/os/readiness` dá 500.

- Esses arquivos **não foram revertidos** (regra do `AGENTS.md`).
- As rodadas de 26 e 27/09 foram feitas em worktrees isoladas.
- Em 27/09, o Codex estava de novo em execução (desde 10:34), mas não escreveu nada na pasta principal durante a rodada.
- Antes de qualquer commit na pasta principal, descubra quem escreveu e decida o que fazer.

Outras alterações não versionadas na pasta principal que **não** entraram em commit:

| Arquivo | Situação |
| --- | --- |
| `veratus_agents/data_discovery.py`, `veratus_agents/catalog.py` | versões antigas escritas por outro processo (acima) |
| `veratus_agents/product_master.py` | agente externo em 25/09: exige material também para joias (decisão abaixo) |
| `integrations/zapier_to_sheets.md` | versão antiga que manda colocar a URL do Zapier no HTML público. Não usar |
| `social/fila-publicacao.csv` | formato alterado, origem desconhecida |
| `docs/data-discovery-2026-09-19.json` | contém nome e endereço do fornecedor. **Não versionar;** apagar ou guardar fora do repositório |

## Estado do repositório

- `origin/main` = `38f50be`. Nada foi enviado nem publicado.
- `main` local: +6 commits (rodadas de 25 e 26/09 organizadas por tema).
- `claude/rodada-2609` (a partir do `main`): +12 commits, decisões do fundador de 26/09. Relatório em `docs/evidence/founder-round-2026-09-26.md`.
- `claude/site-referencias` (a partir de `claude/rodada-2609`): +5 commits de 27/09. Relatório em `docs/evidence/site-round-2026-09-27.md`.
- Flags: `PUBLISH_ENABLED=false`, `PAID_MEDIA_LIVE_WRITES=false`, `PAID_MEDIA_AUTONOMY_MODE=SHADOW`, `WHATSAPP_SEND_ENABLED=false`, `INSTAGRAM_DM_ENABLED=false`, e as 8 flags de canal `false`.
- Produção (`https://veratus.onrender.com`) ainda roda `38f50be`. **O site público ainda mostra as fotos e o vídeo com marca de terceiros** até o deploy.

## Bloqueio principal (atualizado em 27/09)

As fotos e o vídeo dos relógios mostravam ROLEX, a coroa e o selo "ROLEX S.A. GENEVE". Isso incluía o catálogo, o hero, a campanha e os posts sociais.

- O fundador confirmou em 27/09 que as **peças físicas não levam marca de terceiros**.
- Os 47 arquivos estão em `quarantine/third-party-marks/`, fora do site e da imagem Docker. O publicador social recusa essa mídia.
- Relógios aparecem sem foto ("Foto oficial em produção"), **sem preço, sem botão de pedido**, e o Black GMT segue fora da vitrine.
- **Desbloqueio:** fotos reais do item exato, sem marca de terceiros, com fonte no Product Master (`image`, `image_status`).
- Não edite as fotos antigas para apagar a marca.

## Verificar

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

- Testes de navegador (`tests/test_landing_browser.py`) usam o Chrome local e são pulados no CI.
- `tests/test_multiworker_state.py` sobe 2 processos reais. Com `VERATUS_TEST_DATABASE_URL` (PostgreSQL descartável), roda também no PostgreSQL e apaga as tabelas de snapshot desse banco.

## Decisões abertas do fundador

1. Push e deploy: `main` (+6), `claude/rodada-2609` (+12), `claude/site-referencias` (+5). Enquanto não houver deploy, a mídia com marca continua no ar.
2. Fotos reais dos 9 relógios (Black GMT incluído). Com elas: preço R$ 289,90, botão de pedido e Black GMT na vitrine. Os modelos seguem o desenho de modelos conhecidos (Submariner, GMT-Master, Datejust), mesmo sem a marca. Vale uma avaliação jurídica sobre conjunto-imagem antes de anunciar.
3. Posts da fila social usavam a mídia com marca e estão bloqueados. Novos posts precisam de fotos reais ou das joias.
4. Origem registrada com manuseio de 2 dias úteis vs entrega em até 7 dias. Quando o prazo começa a contar?
5. `product_master.py` (material obrigatório para joias no QA): manter ou reverter.
6. Economics: frete pago pela Veratus, taxa de pagamento e tributo (UNVERIFIED). Custo de devolução não modelado.
7. Preços das joias: nenhuma fonte real (9 × NEEDS_PRICING). Para publicar, preencher `sale_price` e `price_source`.
8. Fornecedor no histórico do Git (`commercial_config.py` até `38f50be`) e mídia com marca no histórico: reescrever ou não.
9. `VERATUS_SELLER_DOCUMENT` e `INSTAGRAM_ACCESS_TOKEN` no Render; número do WhatsApp, ADR de tracking, app do Mercado Livre e 14º papel (`paid-acquisition-worker`).

## Não faça

- Não ligue nenhuma flag de envio ou publicação nem faça deploy sem confirmação explícita no momento da ação.
- Não publique preço, anúncio ou post de relógio sem foto real do item exato.
- Não restaure nada de `quarantine/third-party-marks/` para `landing/` nem use essa mídia em post ou anúncio.
- Não "restaure" arquivos que você não alterou; registre aqui.
- Não versione `docs/data-discovery-2026-09-19.json` nem nada com nome, endereço ou cidade do fornecedor.

## Próximo passo sugerido

1. Com o push e o deploy aprovados, publicar as três branches na ordem (`main`, `claude/rodada-2609`, `claude/site-referencias`). Isso tira do ar a mídia com marca.
2. Conferir em produção:
   - `/health`, `/robots.txt` e `/sitemap.xml`;
   - `/os/paid-media/status` (401 sem token);
   - `/integrations/whatsapp/webhook` (403 sem verify token);
   - `/assets/catalog/black-gmt.webp` e `/social/posts/navy-gold.jpg` (404).
3. Rodar `VERATUS_LANDING_URL=https://veratus.onrender.com pytest tests/test_landing_browser.py`.
4. Receber as fotos reais dos relógios e aplicar preço e botão de pedido.
