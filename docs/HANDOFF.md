# HANDOFF — leia antes de editar, atualize antes de sair

**Última atualização:** 27/09/2026 (tarde), Claude Code.
**Trava:** `python scripts/agent_lock.py status`. Só edite com a trava em seu nome (regra no `AGENTS.md`).

## Estado do repositório

- `origin/main` = `38f50be`. **Nada foi enviado nem publicado.**
- `claude/site-referencias` contém tudo: rodadas de 25, 26 e 27/09, **27 commits** sobre `origin/main`. Os commits do `main` local (+6) e de `claude/rodada-2609` (+12) já estão nela.
- Para publicar tudo de uma vez: `git push origin claude/site-referencias:main`. O Render publica o `main` (deploy automático do serviço `veratus-leads`, a conferir no painel). Só com confirmação do fundador no momento da ação.
- Flags: `PUBLISH_ENABLED=false`, `PAID_MEDIA_LIVE_WRITES=false`, `PAID_MEDIA_AUTONOMY_MODE=SHADOW`, `WHATSAPP_SEND_ENABLED=false`, `INSTAGRAM_DM_ENABLED=false`, e as 8 flags de canal `false`.
- Produção (`https://veratus.onrender.com`) ainda roda `38f50be`, **com as fotos e o vídeo com marca de terceiros no ar** até o push.

## Pasta principal (`C:\Users\PC GAMER\Teste`): ainda no `main` antigo

O fast-forward da `main` local para `claude/site-referencias` **não foi feito**. Ele esbarra em arquivos que outro processo escreveu e que não são desta sessão:

| Arquivo | Situação |
| --- | --- |
| `veratus_agents/catalog.py`, `veratus_agents/data_discovery.py` | versões antigas escritas por outro processo em 26/09 (20:39 e 20:55); removem a coleção feminina e a projeção pública; com elas, `pytest` falha e `/os/readiness` dá 500. `catalog.py` impede o fast-forward. |
| `docs/evidence/economics-2026-09-25.md`, `docs/evidence/mercado-livre-readiness-2026-09-25.md`, `docs/insights/posts-2026-09-26.md` | cópias não versionadas que diferem das versões da branch; impedem o fast-forward |
| `veratus_agents/product_master.py` | agente externo em 25/09: exige material também para joias (decisão abaixo) |
| `integrations/zapier_to_sheets.md` | versão antiga que manda colocar a URL do Zapier no HTML público. Não usar |
| `social/fila-publicacao.csv` | formato alterado, origem desconhecida |
| `docs/data-discovery-2026-09-19.json` | contém nome e endereço do fornecedor. **Não versionar;** apagar ou guardar fora do repositório |

Guardar esses arquivos num stash e fazer o fast-forward foi bloqueado pela permissão do Claude Code (risco de perda local). **O fundador decide.** Se aprovar:

```powershell
git stash push -u -m "externo 25-26/09" -- veratus_agents/catalog.py veratus_agents/data_discovery.py docs/HANDOFF.md docs/automation docs/decisions docs/evidence/economics-2026-09-25.md docs/evidence/mercado-livre-readiness-2026-09-25.md docs/evidence/task-contracts-2026-09-25.md docs/evidence/truth-table-2026-09-25.md docs/insights/posts-2026-09-26.md
git merge --ff-only claude/site-referencias
```

O Codex (PID 5968) estava em execução desde 10:34 de 27/09, sem escrever na pasta principal durante a rodada.

## O que mudou em 27/09 (tarde)

| Commit | O que muda |
| --- | --- |
| `3e92e54` | **Relógios à venda.** 9 relógios, Black GMT incluído, com R$ 289,90, "Frete grátis · até 7 dias" e o botão "Pedir" (WhatsApp com produto, referência e intenção de pedido). Sem foto real, cada relógio aparece como **ilustração da própria paleta** (`palette` no Product Master), identificada como "Ilustração da cor". O ponteiro de segundos segue a hora de São Paulo. Hero com R$ 289,90, frete grátis e até 7 dias. |
| `f7855d5` | **Agente de vendas.** Antes, o gate bloqueava qualquer R$, frete grátis e prazo, então o agente não respondia "quanto custa?". Agora `CONFIRMED_SALES_FACTS` libera exatamente preço dos relógios, sem taxas, frete grátis, entrega em até 7 dias e desistência em 7 dias, cada um com `evidence_ref`. Continuam bloqueados: outro valor ou prazo, preço de relógio para joia, marca de terceiros, "réplica", estoque, parcelamento e garantia. As ferramentas não entregam mais custo, fornecedor nem material ao agente. |
| `31cd3c9` | **Fotos reais em um comando**, descrito abaixo. |

## Fotos reais dos relógios

1. Salve uma foto por modelo em `incoming/relogios/<id>.jpg`, por exemplo `ocean-blue.jpg` ou `black-gmt.jpg`. A pasta fica fora do Git.
2. Rode `python scripts/import_watch_photos.py --sem-marca-de-terceiros`.
3. O script recorta em 4:5, salva WebP sem EXIF e marca `REAL_PHOTO` no Product Master. O cartão troca a ilustração pela foto.

Anúncio pago e post de relógio continuam bloqueados até a foto real do item exato (`STOREFRONT_BLOCKERS`). A mídia antiga com marca segue em `quarantine/third-party-marks/`: não restaurar, não editar para apagar a marca.

## Verificar

```powershell
.\.venv\Scripts\python.exe -m pytest -q          # 221 passed em 27/09 (worktree claude/site-referencias)
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

- Os testes de navegador (`tests/test_landing_browser.py`) usam o Chrome local e são pulados no CI.
- `tests/test_multiworker_state.py` sobe 2 processos reais.

## Decisões abertas do fundador

1. **Push para o GitHub, que o Render publica.** Isso tira do ar a mídia com marca e coloca os relógios à venda.
2. Fast-forward da pasta principal (stash acima).
3. Fotos reais dos 9 relógios, para anúncio, post e troca da ilustração. Os modelos seguem o desenho de modelos conhecidos. Vale uma avaliação jurídica de conjunto-imagem antes de anunciar.
4. Origem registrada com manuseio de 2 dias úteis vs entrega em até 7 dias. Quando o prazo começa a contar?
5. `product_master.py` (material obrigatório para joias no QA): manter ou reverter.
6. Economics: frete pago pela Veratus, taxa de pagamento e tributo (UNVERIFIED).
7. Preços das joias: nenhuma fonte real (9 × NEEDS_PRICING).
8. Fornecedor e mídia com marca no histórico do Git: reescrever ou não.
9. `VERATUS_SELLER_DOCUMENT`, `INSTAGRAM_ACCESS_TOKEN` e `OPENAI_API_KEY` no Render; número do WhatsApp Cloud API; formas de pagamento, que o agente ainda manda confirmar com a equipe.

## Não faça

- Não ligue nenhuma flag de envio ou publicação nem faça push ou deploy sem confirmação explícita no momento da ação.
- Não use a mídia de `quarantine/third-party-marks/` nem edite fotos para apagar marcas.
- Não "restaure" arquivos que você não alterou; registre aqui.
- Não versione `docs/data-discovery-2026-09-19.json` nem nada com nome, endereço ou cidade do fornecedor.

## Próximo passo sugerido

1. Com o push aprovado: `git push origin claude/site-referencias:main`.
2. Conferir em produção:
   - `/health`;
   - a vitrine com 9 relógios e o preço;
   - `/assets/catalog/black-gmt.webp` e `/social/posts/navy-gold.jpg` devem dar 404;
   - `VERATUS_LANDING_URL=https://veratus.onrender.com pytest tests/test_landing_browser.py`.
3. Receber as fotos reais e rodar o importador.
