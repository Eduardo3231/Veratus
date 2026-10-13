# HANDOFF — leia antes de editar, atualize antes de sair

**Última atualização:** 13/10/2026, Claude Code (canais, atendimento autônomo e redes sociais).
**Trava:** `python scripts/agent_lock.py status`. Só edite com a trava em seu nome (regra no `AGENTS.md`).

## Rodada de 13/10 — canais, atendimento autônomo e redes sociais

O fundador aprovou a seção 0 do prompt nesta sessão, com os valores recomendados: papéis novos, atendimento em `SHADOW`, Pix + link do Mercado Pago com juros pagos pelo cliente e desconto máximo de R$ 0. A Fase 7 (iPhones) fica para depois do primeiro teste.

> **Pacote do fundador pendente.** Os arquivos do pacote (`config/channel_fees_2026-10.json` com fontes, `tests/golden/channel_economics_cases.json`, `knowledge/`, `social/calendario-2026-10.*` e as 33 artes em `landing/social/posts/2026-10/`) estavam só no VS Code local e não chegaram ao repositório. O código está pronto para recebê-los; os arquivos versionados hoje são provisórios e mínimos, marcados `PLACEHOLDER`/`UNVERIFIED`. **Lembrete ao fundador: entregar o pacote e as skills de venda (`knowledge/skills/*.md`).**

### Fase 1 — Economia por canal

- `veratus_agents/channel_economics.py`: `load_fee_config` (schema `veratus.channel_fees/v1`, objetos imutáveis), `evaluate`, `price_for_target_margin`, `price_to_match` (preços terminados em ,90: varredura de real em real abaixo de R$ 250, busca binária acima) e `channel_price_plan`. Os tipos de produto são `jewelry`, `watch` e `smartphone`. O status é `COMPLETE`, `ESTIMATED` ou `INCOMPLETE`; um relógio em marketplace com origem não confirmada fica `BLOCKED_BY_ORIGIN`.
- `custo_maximo` = sobra − margem-alvo × preço. `cpa_maximo` = lucro − margem-alvo × preço. A margem-alvo padrão (20%) é `ASSUMPTION` no arquivo.
- `config/channel_fees_2026-10.json` é **provisório**: valores de memória pública, todos `UNVERIFIED`; só o frete direto do relógio (R$ 25) é `ASSUMPTION`. Nenhum canal chega a `COMPLETE` até o arquivo do fundador substituir este. Se o arquivo do pacote usar outro formato, adapte o loader (o formato esperado está na docstring do módulo).
- `paid_media.calculate_economics(..., channel=)`: taxa do canal, taxa de pagamento, frete, tributo, devoluções e embalagem vêm do motor. O worker usa `PAID_MEDIA_ECONOMICS_CHANNEL` (padrão `direct_card`, o mais caro). A autonomia continua exigindo `COMPLETE`.
- Rotas com `X-Veratus-Admin-Token`:
  - `GET /os/economics/<sku>?channel=`;
  - `POST /os/economics/simulate`;
  - `GET /os/pricing/channel-plan/<sku>`;
  - `POST /os/pricing/channel-plan/<sku>/request`, que abre um `PRICE_APPROVAL` e não aplica nada.
- Relógio em rascunho de marketplace: `prepare_listing` bloqueia com `NEEDS_REAL_PHOTO` e/ou `ORIGIN_UNCONFIRMED` (`commercial_config.watch_channel_blockers`).
- Testes: `tests/test_channel_economics.py`. Os casos dourados ficam *skipped* até o arquivo existir; as bordas (R$ 49,99/50, 78,99/79, 79,99/80) usam o arquivo provisório.
- **Custo real versionado (reportado, não apagado):** `OFFICIAL_PRODUCT_COST_BRL = 65.00` em `veratus_agents/commercial_config.py`, repetido em `marketplace_ops.py`, `data_discovery.py`, testes e `docs/evidence/founder-round-2026-09-26.md`. O repositório é público. Decisão do fundador: mover para o Product Master (banco) e trocar os testes por custo fictício.
- `scripts/agent_lock.py` ganhou bit executável no Git: o Ruff no Linux (CI) acusava EXE001.

## Conexões de marketplace (28/09)

O fundador vende ou vai vender em Mercado Livre, Shopee e Amazon. Os três conectam em **modo leitura**; a publicação continua bloqueada pelas flags.

Comando único: `.\.venv\Scripts\python.exe scripts\conectar_marketplace.py <mercado-livre|shopee|amazon>`.
- Pede `VERATUS_ADMIN_TOKEN` sem ecoar.
- Espera o Render acordar.
- Diz o que falta configurar.
- Roda a inspeção do agente.

| Canal | O fundador faz | O sistema faz |
| --- | --- | --- |
| Mercado Livre | App ID e Secret Key no Render (`MERCADO_LIVRE_CLIENT_ID`, `MERCADO_LIVRE_CLIENT_SECRET`). Redirect URI no app: `https://veratus.onrender.com/integrations/mercado-livre/oauth/callback` | OAuth já existente. Após autorizar, o gerente roda a verificação: conta, categoria, atributos obrigatórios e busca por SKU |
| Shopee | App no Shopee Open Platform. Cadastra `SHOPEE_PARTNER_ID` e `SHOPEE_PARTNER_KEY` no Render. Domínio de redirect do app: `veratus.onrender.com` | `/integrations/shopee/oauth/start` → autorização da loja → `/oauth/callback/<state>` troca o código. Token de 4 h renovado sozinho, cifrado no canal `shopee`. Inspeção: dados da loja e categoria de relógio |
| Amazon | App privado no Seller Central → Autorizar. Cadastra `AMAZON_SP_LWA_CLIENT_ID`, `AMAZON_SP_LWA_CLIENT_SECRET` e `AMAZON_SP_REFRESH_TOKEN` no Render | Token LWA (1 h, em cache), endpoint NA, marketplace `A2Q3Y263D00KWC`. Inspeção: participação no Brasil e product type de relógio |

- **Configuração no Render:**
  - `VERATUS_TOKEN_ENCRYPTION_KEY` precisa existir (chave Fernet). O comando explica como gerar se faltar.
  - `SHOPEE_API_HOST`, `SHOPEE_REDIRECT_URI` e `AMAZON_SP_ENDPOINT`/`AMAZON_SP_MARKETPLACE_ID` são opcionais; o código tem os padrões.
  - As variáveis da Amazon estão declaradas no `render.yaml` e no `.env.example`. As chaves ficam com `sync: false`, e `AMAZON_ENABLED`/`AMAZON_PUBLISH_ENABLED` com `false`.
- **Não verificado ao vivo:** nenhuma das três contas foi conectada ainda. O host da Shopee para contas brasileiras (`partner.shopeemobile.com`) segue a documentação pública; se a Shopee indicar outro host, basta mudar `SHOPEE_API_HOST`.
- **Amazon Agent (15º agente, 28/09):** canal `amazon` registrado (desligado), `amazon-agent` sob o `distribution-supervisor`, rascunho de anúncio com product type e GTIN a confirmar, canal `amazon` no Orders Ledger. O gerente inclui a Amazon na auditoria de conexões e nos comandos ("prepare ... Amazon").
- **Criação de anúncio** segue ausente nos quatro canais de venda. Os relógios não devem ir a anúncio sem foto real (`STOREFRONT_BLOCKERS`).
- **Pasta principal:** o Codex deixou alterações não commitadas em `integrations/webhook.py`, `render.yaml`, `.env.example`, `veratus_agents/paid_media.py`, `veratus_agents/meta_webhooks.py`, `tests/test_paid_media.py` e na ADR de tracking. Commitar ou descartar essas alterações é decisão do fundador. Só depois a pasta principal pode puxar a branch `claude/marketplaces`, que também altera `webhook.py`.
- **Palco dos modelos** (branch `claude/experiencia-modelos`): pronto e testado, **não publicado** por decisão do fundador em 28/09.

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
