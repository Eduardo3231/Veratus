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

### Fase 2 — Relógios prontos para o teste

- O gate (`policy.py`) bloqueia, para todo produto:
  - marca de terceiros, réplica, clone, primeira linha e "inspirado em";
  - movimento ou mecanismo, resistência à água, medidas (mm, cm, diâmetro) e garantia afirmada;
  - qualquer menção a origem, importação, alfândega ou "taxado";
  - claims de material de joia: ouro, prata, banhado, folheado, semijoia, zircônia, hipoalergênico;
  - urgência ou escassez ("só hoje", "por tempo limitado") e oferta de desconto ou cupom (`DISCOUNT_LIMIT_BRL=0`).
- `commercial_config.watch_economics_snapshot(channel)`: o EconomicsSnapshot dos relógios vem do motor. Frete direto R$ 25 = `ASSUMPTION`; pagamento e tributo `UNVERIFIED`, então o worker de mídia paga continua sem CPA final. `WATCH_ECONOMICS_SNAPSHOT` fica como registro histórico de 26/09.
- `watch_channel_blockers(produto, canal)`:
  - `NEEDS_REAL_PHOTO` em qualquer canal sem `REAL_PHOTO`;
  - `ORIGIN_UNCONFIRMED` nos marketplaces enquanto `delivery-vs-registered-origin` estiver pendente. Para liberar, o status passa a `ANSWERED_DOMESTIC` em `FOUNDER_OPEN_QUESTIONS`.
- Fotos reais: o fluxo não mudou (`incoming/relogios/<id>.jpg` → `scripts/import_watch_photos.py --sem-marca-de-terceiros`). A quarentena não mudou.
- Testes: `tests/test_watch_readiness.py`.

### Fase 3 — Atendimento autônomo (Sales Agent V2)

- **Módulos:**
  - `veratus_agents/conversations.py`: motor, classificador, níveis, gate, fila, disjuntor, alertas, prontidão, métricas e eval;
  - `veratus_agents/channel_adapters.py`: adaptadores e alerta ao fundador;
  - `veratus_agents/payments.py`: Mercado Pago.
- **Pipeline:** classificar → contexto (`knowledge/`, produto, origem, pedido) → rascunho → revisor (no modelo real) → gate → decidir → adaptador → auditoria (`cv_events`). Se o gate reprova, há uma reescrita; se reprova de novo, a conversa vai para `HUMAN` com `IA_TRAVADA`. Mensagem repetida pelo webhook é processada uma vez só.
- **Níveis:** `CONVERSATION_MODE_<CANAL>` (padrão `SHADOW`).
  - `AUTO_SAFE` envia sozinho saudação, catálogo, produto, preço, frete, prazo e como pedir.
  - `AUTO` envia também fechamento, coleta de dados, link, status e rastreio.
  - Risco 2 e intenção desconhecida nunca saem sozinhos.
  - Mesmo em `AUTO`, nada sai sem a chave do canal (`WHATSAPP_SEND_ENABLED`, `INSTAGRAM_MESSAGING_ENABLED`, `MERCADO_LIVRE_ANSWERS_ENABLED`). Quando o envio é bloqueado, o rascunho volta para a fila com o motivo.
- **Gatilhos de passagem ao fundador** (`HUMAN`): atendente, reclamação/Procon/Reclame Aqui, problema de pagamento (inclusive comprovante), fora da política (desconto pela 2ª vez, troca fora do prazo, entrega expressa), fraude (enviar antes de pagar, pagar fora do link, trocar endereço depois de pago), só uma pessoa faz (nota fiscal, ligação, personalização, atacado), fora de vendas, IA travada (3 mensagens sem avanço ou 2 reprovações do gate).
  - **Interpretação registrada:** o 9º gatilho, fato não confirmado, *não* põe a conversa em `HUMAN`. O agente responde "a equipe confirma" e o fundador recebe o alerta, mas a conversa continua (o prompt diz que ele "não bloqueia o resto da conversa"). Os outros oito param a conversa.
- **Alerta ao fundador:**
  - template de utilidade `alerta_fundador` para `FOUNDER_ALERT_WHATSAPP` (motivo, resumo de até 3 linhas sem telefone/e-mail, sugestão);
  - fallback por e-mail (`FOUNDER_ALERT_EMAIL` + `SMTP_*`);
  - sem nada configurado, o alerta fica em `GET /os/conversations/alerts`.
  - A conversa volta para `BOT` com `POST /os/conversations/<canal>/<id>/state` `{"state":"BOT","actor":...}`.
- **Disjuntor:** duas reprovações do gate em uma hora, ou uma reclamação, rebaixam o canal um nível (`cv_channel_caps`) e alertam o fundador. `GET /os/conversations/readiness` calcula os critérios (50 conversas com 90% aprovadas sem edição e nenhuma reprovação; uma semana com 95%; 10 vendas sem erro) e abre um `AUTONOMY_CHANGE`. Subir de nível é mudar a variável no Render, nunca automático.
- **Pagamento:**
  - preferência com `external_reference` = id do pedido, parcelado com juros do cliente;
  - `POST /integrations/mercado-pago/webhook` valida `x-signature` (HMAC, tolerância de 10 min) e lê o pagamento de volta antes de `PAID`: `approved`, mesmo pedido, valor ≥ pedido, BRL;
  - comprovante enviado pelo cliente vira passagem ao fundador, nunca confirmação.
- **Dados do cliente:** só depois do "sim" (fechamento), cifrados com `VERATUS_TOKEN_ENCRYPTION_KEY` em `cv_customer_data`. Nunca entram na auditoria, no log nem no Orders Ledger. **Proposta de retenção para o fundador decidir:** apagar 90 dias depois da entrega (`ConversationStore.purge_customer_data`); as obrigações fiscais ficam com o documento fiscal, não com a conversa.
- **Adaptadores:**
  - WhatsApp: atual, mais templates de utilidade e marketing só com opt-in registrado (`marketing_allowed`);
  - Instagram Direct: campo `messages` do webhook atual, `DirectInbox` cifrado, janela de 24 h, tag `HUMAN_AGENT` só para texto escrito por pessoa;
  - Mercado Livre: perguntas (`/my/received_questions/search`, `/answers`) e pós-venda (`/messages/packs`). O gate do canal proíbe link, @, telefone, e-mail e convite para outro canal;
  - Shopee, TikTok Shop e Amazon: `API_CONTRACT_UNVERIFIED`, o rascunho vai para a fila para o fundador colar.
- **Rotas** (admin):
  - `POST /os/conversations/process` `{channel}`: processa as mensagens novas. Precisa de um cron no Render a cada minuto, que é ação externa e **não foi criado**;
  - `GET /os/conversations`, `/queue`, `/metrics`, `/readiness`, `/alerts` e `/adapters`;
  - `POST /os/conversations/queue/<id>/approve|reject`;
  - `POST /os/conversations/<canal>/<id>/state`.
- **Métricas por canal e por nível:** tempo da primeira resposta, aprovadas com e sem edição, passagens por motivo, conversa → pedido, pedidos pagos e reprovações do gate.
- **`test_no_cold_outreach.py`:** responder no Direct exige endereçar pelo id de quem escreveu. O teste passou a permitir só `veratus_agents/channel_adapters.py` (como já fazia com `whatsapp.py`) e ganhou um teste novo provando que esse adaptador recusa quem nunca escreveu ou está fora da janela.
- **Testes:**
  - `tests/test_conversations.py`: do "oi" ao link em `AUTO` com cliente falso; cada gatilho; disjuntor; pagamento; contrato dos adaptadores;
  - pipeline pelas rotas com `requests.Session.post` e `requests.post` falhando em qualquer chamada: nada sai com as chaves desligadas.

### Fase 4 — Repertório (`knowledge/`)

- `veratus_agents/knowledge.py` carrega e valida `voz.md`, `roteiro.md`, `objecoes.md`, `fatos.yaml`, `ofertas.yaml` e `skills/*.md`.
- `CONFIRMED_SALES_FACTS` agora vem de `fatos.yaml` (só `CONFIRMED`), com os mesmos `evidence_ref`; os fatos embutidos ficam como reserva.
- Fatos novos confirmados:
  - `relogios-modelos`;
  - `relogios-sem-marca-de-terceiros`;
  - `formas-pagamento`: Pix ou link do Mercado Pago, juros do parcelado com o cliente (seção 0).
- Pendentes, que viram "a equipe confirma":
  - material, especificações, origem/prazo, preço das joias e garantia;
  - número de parcelas, que segue em `SALES_MUST_CONFIRM_WITH_TEAM`.
- `ofertas.yaml`: só `ACTIVE` dentro da validade chega ao agente. O exemplo é `DRAFT`, um kit sem desconto.
- `python -m veratus_agents.knowledge lint`: reprova pressão, escassez, material, garantia, origem, marca de terceiros, desconto e fornecedor (o gate ganhou "menção a fornecedor"). Linhas com "nunca", "não diga", "proibido" ou "evite" são regras e não são reprovadas.
- `python -m veratus_agents.knowledge eval`: roda `conversas-teste.yaml` com o modelo simulado (`TemplateSalesModel`); `--live` usa o modelo real e tem custo. Ambos rodam no CI (`verify.yml`) e o CI quebra em caso obrigatório.
- **Provisório:** `voz.md`, `roteiro.md`, `objecoes.md` e `conversas-teste.yaml` foram escritos nesta sessão a partir do prompt atual do agente. `skills/` está vazio. **Lembrete ao fundador: entregar as skills e o roteiro de venda do pacote.**
- `PyYAML` entrou em `integrations/requirements.txt`. O Dockerfile copia `config/` e `knowledge/`.

### Fase 5 — Agendador de redes sociais

- `veratus_agents/social_scheduler.py`:
  - tabelas `scheduled_posts`, `social_approvals`, `social_publications`, `social_media_keywords`, `social_insights` e `social_events`;
  - status `DRAFT` → `PENDING_APPROVAL` → `APPROVED` → `PUBLISHING` → `PUBLISHED` | `FAILED` | `NEEDS_MANUAL` | `NEEDS_VIDEO` | `BLOCKED`.
- **Importação:** `python -m veratus_agents.social_scheduler import social/calendario-2026-10.json`.
  - Aceita lista ou `{"itens": [...]}`, com nomes em português ou inglês (`data_hora`, `formato`, `midias`, `legenda`, `palavras_chave`, `produtos`, `status`).
  - Horário sem fuso = Brasília. Mídia sem URL vira `https://veratus.onrender.com/social/posts/2026-10/<arquivo>`.
  - Vídeo entra `NEEDS_VIDEO`; item só de TikTok entra `NEEDS_MANUAL`.
  - Relógio sem `REAL_PHOTO`, linha de iPhone e mídia em quarentena entram `BLOCKED`, mesmo que o calendário esqueça.
  - O calendário real não está no repositório (pacote pendente). O teste com um calendário sintético de mesma composição dá 44 itens: 31 para aprovação, 7 vídeos e 6 bloqueados.
- **Aprovação semanal:** `request-approval` abre um `PUBLISH_APPROVAL` por semana com os `content_hash`. O fundador aprova em `/os/approvals/<id>/approve`. Editar o item depois disso (`edit_item`) tira a aprovação.
- **Publicação:** `run-due`, por cron do Render a cada 5 min (ação externa, **não criado**). Com `SOCIAL_PUBLISH_ENABLED=false`, só devolve a prévia, sem chamar a Meta e sem item bloqueado. Ligado:
  - confere se cada mídia responde 200 `image/jpeg` e não está na quarentena;
  - publica por `meta_content_publisher` (estendido com `create_social_container` para IMAGE/CAROUSEL/STORY, `publish_facebook_page` e `read_facebook_permalink`) e lê o permalink de volta;
  - é idempotente por id + `content_hash`;
  - faz 3 tentativas (espera de 10, 20 e 40 min) e depois `FAILED` + alerta.
  - Story na Página do Facebook fica `NEEDS_MANUAL`. TikTok: `TikTokDraftAdapter` `UNVERIFIED`, devolve `NEEDS_MANUAL`.
- **Palavras-chave por post:** `instagram_comment_rules.json` ganhou `keyword_replies` (QUERO, CATÁLOGO, PRESENTE, TREVO, CORAÇÃO, LUZ, IPHONE). O id da mídia vem do registro de publicação, e a regra vale só no post que pediu a palavra (`CommentReplyQueue.enqueue(..., media_rules=)`). O envio continua dependendo de `INSTAGRAM_DM_ENABLED`.
- **Relógios (Fase 2.6):** `refresh-blocks` marca `AGUARDA_ARTE_COM_FOTO` quando o modelo ganha `REAL_PHOTO` e registra o pedido ao Creative Agent. A arte nova entra por `edit_item(media=...)` e o item volta para `PENDING_APPROVAL`. **A recomposição automática da arte não foi implementada:** o layout das joias está nas artes do pacote, que não chegaram.
- **Métricas:** `insights` lê alcance, salvamentos, comentários e compartilhamentos em +24 h e +7 dias. `report --week` gera o relatório semanal, e `insights_csv` exporta no formato de `social/medicao.csv`.
- **Rota:** `GET /os/social/posts`.
- Variáveis: `SOCIAL_PUBLISH_ENABLED=false` e `META_PAGE_ID` (vazio). O Dockerfile copia `social/`.

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
