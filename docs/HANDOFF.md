# HANDOFF — leia antes de editar, atualize antes de sair

**Última atualização:** 28/09/2026, Claude Code (Meta Pixel).
**Trava:** `python scripts/agent_lock.py status`. Só edite com a trava em seu nome (regra no `AGENTS.md`).

## Estado do repositório

- **Publicado em 27/09 com autorização do fundador:** `git push origin main` até `4f9cb2f`, incluindo a restauração das imagens dos nove relógios.
- Produção (`https://veratus.onrender.com`) conferida depois do deploy:
  - `/health` 200;
  - `/catalog.json` com `price_brl` nos 9 relógios;
  - `/catalog.json` aponta os nove relógios para imagens distintas em `assets/catalog/`;
  - os nove arquivos WebP respondem 200; o MIME explícito `image/webp` foi acrescentado ao servidor na rodada final;
  - `VERATUS_LANDING_URL=https://veratus.onrender.com pytest tests/test_landing_browser.py`: 12 passed, 1 skipped.
- Pasta principal: `main`; `pytest -q` 221 passed e teste de produção da landing 12 passed, 1 skipped.
- Flags: `PUBLISH_ENABLED=false`, `PAID_MEDIA_LIVE_WRITES=false`, `PAID_MEDIA_AUTONOMY_MODE=SHADOW`, `WHATSAPP_SEND_ENABLED=false`, `INSTAGRAM_DM_ENABLED=false`, e as 8 flags de canal `false`.

## Arquivos de outro processo na pasta principal

Com autorização do fundador (27/09), foram guardados no stash "externo 25-26/09 … autorizado pelo fundador" (`git stash list`; recuperar com `git stash show -p` ou `git stash pop`):

- as versões antigas de `veratus_agents/catalog.py` e `veratus_agents/data_discovery.py`. Elas eram de 26/09, às 20:39 e 20:55, removiam a coleção feminina e a projeção pública e faziam `/os/readiness` dar 500;
- cópias não versionadas de docs de 25 e 26/09.

Continuam na pasta, sem commit e sem alteração desta sessão:

| Arquivo | Situação |
| --- | --- |
| `veratus_agents/product_master.py` | agente externo em 25/09: exige material também para joias (decisão abaixo); os 221 testes passam com ele |
| `integrations/zapier_to_sheets.md` | versão antiga que manda colocar a URL do Zapier no HTML público. Não usar |
| `social/fila-publicacao.csv` | formato alterado, origem desconhecida |
| `docs/data-discovery-2026-09-19.json` | contém nome e endereço do fornecedor. **Não versionar;** apagar ou guardar fora do repositório |

O Codex (PID 5968) estava em execução desde 10:34 de 27/09, sem escrever na pasta principal durante a rodada.

## O que mudou em 27/09 (tarde)

| Commit | O que muda |
| --- | --- |
| `3e92e54` | **Relógios à venda.** 9 relógios, Black GMT incluído, com R$ 289,90, "Frete grátis · até 7 dias" e o botão "Pedir" (WhatsApp com produto, referência e intenção de pedido). Sem foto real, cada relógio aparece como **ilustração da própria paleta** (`palette` no Product Master), identificada como "Ilustração da cor". O ponteiro de segundos segue a hora de São Paulo. Hero com R$ 289,90, frete grátis e até 7 dias. |
| `f7855d5` | **Agente de vendas.** Antes, o gate bloqueava qualquer R$, frete grátis e prazo, então o agente não respondia "quanto custa?". Agora `CONFIRMED_SALES_FACTS` libera exatamente preço dos relógios, sem taxas, frete grátis, entrega em até 7 dias e desistência em 7 dias, cada um com `evidence_ref`. Continuam bloqueados: outro valor ou prazo, preço de relógio para joia, marca de terceiros, "réplica", estoque, parcelamento e garantia. As ferramentas não entregam mais custo, fornecedor nem material ao agente. |
| `31cd3c9` | **Fotos reais em um comando**, descrito abaixo. |
| `4f9cb2f` | **Imagens dos nove relógios restauradas.** Cada criativo voltou ao modelo correspondente; o Product Master distingue o criativo de vitrine da foto real exigida para canais externos. |

## Meta Pixel (28/09)

O Pixel `1633870688525258`, pedido pelo fundador, está em `index.html`, `condicoes-de-compra.html` e `privacy.html`.

- **Eventos:**
  - `PageView` em toda página;
  - `ViewContent` ao abrir um produto;
  - `Contact` em qualquer clique num link do WhatsApp, com `content_ids`, `content_name` e, nos relógios, `value: 289.9` e `currency: BRL`.
- O texto da mensagem do WhatsApp nunca vai para a Meta.
- **Não carrega em `localhost`/`127.*`.** Os testes de navegador também bloqueiam `connect.facebook.net` e `www.facebook.com/tr`, mesmo contra produção, para visitas de teste não entrarem nas métricas.
- `privacy.html` ganhou a seção "Cookies e medição de anúncios".
- **Publicado em 28/09** (`fe4de26`, push autorizado pelo fundador).
- **Verificado em produção:**
  - o HTML traz o código;
  - `fbevents.js` e a configuração do Pixel carregam;
  - `fbq.getState()` mostra o Pixel `1633870688525258` com `eventCount: 1` (PageView).
- `ViewContent` e `Contact` estão cobertos pelo teste de navegador (`test_meta_pixel_tracks_product_views_and_whatsapp_contacts`).
- **Não verificado ao vivo:**
  - a chegada dos eventos no Gerenciador de Eventos (Testar eventos). O navegador automatizado não mostrou a requisição `/tr`;
  - a variável `META_PIXEL_ID` no Render, que o worker de mídia paga lê.
- Banner de consentimento de cookies: não existe. Decisão do fundador.
- A ADR de tracking continua pendente; o Pixel cobre a parte "Contact" da opção 1.

## Fotos reais dos relógios

**Publicado e verificado em 27/09:** por ordem direta do fundador, os nove criativos de catálogo voltaram à vitrine e foram religados aos modelos Arctic White, Ocean Blue, Black GMT, Royal Blue, Platinum Classic, Emerald Signature, Silver Prestige, Polar Blue e Bronze Heritage. Eles ficam em `landing/assets/catalog/`, todos com 1122×1402. O Product Master os classifica como `CATALOG_CREATIVE` e mantém `image_status: NEEDS_REAL_PHOTO`; assim, aparecem no site sem liberar anúncios, posts ou listings como se fossem fotos reais da peça.

1. Salve uma foto por modelo em `incoming/relogios/<id>.jpg`, por exemplo `ocean-blue.jpg` ou `black-gmt.jpg`. A pasta fica fora do Git.
2. Rode `python scripts/import_watch_photos.py --sem-marca-de-terceiros`.
3. O script recorta em 4:5, salva WebP sem EXIF e marca `REAL_PHOTO` no Product Master. O cartão troca a ilustração pela foto.

Anúncio pago e post de relógio continuam bloqueados até a foto real do item exato (`STOREFRONT_BLOCKERS`). As demais mídias antigas seguem em `quarantine/third-party-marks/`: não restaurar nem editar para apagar marcas.

## Verificar

```powershell
.\.venv\Scripts\python.exe -m pytest -q          # 226 passed em 28/09 (pasta principal)
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

- Os testes de navegador (`tests/test_landing_browser.py`) usam o Chrome local e são pulados no CI.
- `tests/test_multiworker_state.py` sobe 2 processos reais.

## Decisões abertas do fundador

1. Fotos reais dos 9 relógios para anúncio, post e listings externos. Os criativos antigos voltaram somente à vitrine por decisão direta do fundador; continuam bloqueados nos canais automáticos.
2. Origem registrada com manuseio de 2 dias úteis vs entrega em até 7 dias. Quando o prazo começa a contar?
3. `product_master.py` (material obrigatório para joias no QA): manter ou reverter.
4. Economics: frete pago pela Veratus, taxa de pagamento e tributo (UNVERIFIED).
5. Preços das joias: nenhuma fonte real (9 × NEEDS_PRICING).
6. Fornecedor e mídia com marca no histórico do Git: reescrever ou não.
7. `VERATUS_SELLER_DOCUMENT`, `INSTAGRAM_ACCESS_TOKEN` e `OPENAI_API_KEY` no Render; número do WhatsApp Cloud API; formas de pagamento, que o agente ainda manda confirmar com a equipe.

## Não faça

- Não ligue nenhuma flag de envio ou publicação nem faça push ou deploy sem confirmação explícita no momento da ação.
- Não use a mídia de `quarantine/third-party-marks/` fora das nove exceções de vitrine registradas em `manifest.json`; não edite fotos para apagar marcas.
- Não "restaure" arquivos que você não alterou; registre aqui.
- Não versione `docs/data-discovery-2026-09-19.json` nem nada com nome, endereço ou cidade do fornecedor.

## Próximo passo sugerido

1. Receber as fotos das peças (`incoming/relogios/<id>.jpg`) e rodar o importador para liberar canais externos.
