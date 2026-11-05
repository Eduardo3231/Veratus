# API do Veratus OS

O início deste arquivo descreve a entrada do Sales Agent V1 (`/agent/*`). O mapa completo das rotas de hoje, com os 25 agentes, o Harvey, os marketplaces, as redes sociais e o expediente, está em "Mapa das rotas", no fim.

O serviço Flask existente agora expõe uma camada protegida para o agente. Ela é intencionalmente separada do `/webhook` público de captação.

## Saúde

`GET /agent/health`

Retorna `configured` ou `not_configured`, informa em `missing_configuration` somente os nomes das configurações ausentes ou inválidas e confirma que envio externo está desabilitado. Esse status valida a forma da URL PostgreSQL, mas não faz uma chamada ao banco ou ao modelo. Não revela credenciais. O serviço principal também expõe `GET /health`.

## Criar rascunho

`POST /agent/sales/draft`

Header obrigatório:

```text
X-Veratus-Agent-Key: <VERATUS_AGENT_SHARED_SECRET>
```

Payload normalizado:

```json
{
  "customer_ref": "cliente-interno-123",
  "message": "Gostei do Ocean Blue. Qual o valor?",
  "source": "whatsapp",
  "product_hint": "Ocean Blue",
  "event_id": "evento-unico-do-provedor-123"
}
```

`event_id` é opcional, mas deve ser enviado por integrações reais. Repetições do mesmo evento, cliente interno e origem reutilizam o run. A API exige JSON, limita o corpo a 8 KiB e a mensagem a 4.000 caracteres; aplica rate limit e retorna `request_id`, `run_id`, estado, rascunho, revisão, gate e métricas. `customer_ref` deve ser um identificador interno estável; não use telefone ou e-mail como identificador. Nenhuma mensagem externa é enviada.

## Revisão humana

`GET /agent/runs?status=pending_review`, `GET /agent/runs/<run_id>` e `POST /agent/runs/<run_id>/decision`

Header obrigatório:

```text
X-Veratus-Admin-Token: <VERATUS_ADMIN_TOKEN>
```

Decisão:

```json
{
  "decision": "approve",
  "actor": "operador-eduardo",
  "approved_reply": "Posso confirmar o valor e a disponibilidade atuais com a equipe.",
  "note": "Texto final conferido para esta conversa."
}
```

A decisão só é aceita quando o run está em `pending_review`. Uma nova decisão recebe `409`. Aprovações exigem o texto final e a identidade interna do operador. O sistema registra data, política e hash SHA-256 do texto aprovado em um evento de auditoria. A aprovação altera apenas o estado interno. O V1 não possui ferramenta de envio para WhatsApp. Os dois tokens devem ser diferentes e nunca devem ser expostos no navegador.

## Teste local sem cliente real

Configure `OPENAI_API_KEY`, `VERATUS_SESSION_SALT`, `VERATUS_AGENT_SHARED_SECRET` e `VERATUS_ADMIN_TOKEN` no `.env` local e execute `python -m integrations.webhook`. A chamada de draft executa dois agentes e consome API; use apenas mensagens de teste. O serviço Flask roda na porta `5000`. A API FastAPI na porta `8000` serve somente para consulta do catálogo e Gemini opcional.

Com `DATABASE_URL`, runs e memória usam PostgreSQL. Sem essa variável, o modo local usa SQLite em `VERATUS_AGENT_RUNTIME_DIR`. O endpoint `/agent/health` só informa `configured` quando PostgreSQL e os demais segredos estão presentes.

## Limites de execução

- `VERATUS_AGENT_MAX_TURNS`: turnos por agente;
- `VERATUS_AGENT_MAX_OUTPUT_TOKENS`: saída máxima por chamada;
- `VERATUS_AGENT_MAX_TOTAL_TOKENS`: teto observado para Sales + Reviewer;
- `VERATUS_AGENT_REQUEST_TIMEOUT_SECONDS`: timeout de cada requisição ao modelo;
- `VERATUS_AGENT_WORKFLOW_TIMEOUT_SECONDS`: prazo global aplicado no servidor Linux;
- `VERATUS_AGENT_MODEL_MAX_RETRIES`: tentativas adicionais, limitado pelo código a duas;
- `VERATUS_AGENT_SESSION_HISTORY_LIMIT`: quantidade máxima de itens recuperados da memória.

O `metrics` persistido contém duração, uso de tokens e nomes de eventos de tools. Não contém argumentos das tools, prompts internos ou credenciais.

## Copiloto de live

`POST /agent/live/draft` usa o mesmo header `X-Veratus-Agent-Key` e recebe:

```json
{
  "live_session_id": "live-2026-09-19-01",
  "viewer_ref": "viewer-interno-42",
  "message": "Qual modelo combina com um estilo mais clássico?",
  "product_hint": "Platinum Classic",
  "event_id": "comentario-unico-42"
}
```

O resultado entra na mesma fila de revisão e nunca é publicado automaticamente.

## Métricas de aquisição

`POST /agent/metrics/records` registra um fechamento imutável por `event_id`. `GET /agent/metrics/summary` aceita filtros opcionais `from`, `to`, `channel` e `campaign` e devolve CTR, CPC, CPL, CPA, conversões, ROAS, ticket médio e margem de contribuição. Os dois endpoints exigem `X-Veratus-Admin-Token`. Consulte `docs/growth-measurement.md` para as definições e o processo de importação.

## Veratus OS e Product Master

Os endpoints `/os/*` usam o mesmo token administrativo. A implementação inclui cadastro idempotente com SKU, atualização, lista, eventos de auditoria, máquina de estados, CEO Override e cálculo determinístico de preço. A aprovação exige evidência para os campos comerciais essenciais. O contrato completo e exemplos estão em `docs/veratus-os.md`.

```text
POST  /os/products
GET   /os/products
GET   /os/products/<sku>
PATCH /os/products/<sku>
POST  /os/products/<sku>/transition
POST  /os/products/<sku>/override
GET   /os/products/<sku>/events
POST  /os/pricing/calculate
```

## Mapa das rotas (atualizado em 05/11/2026)

Autenticação: toda rota `/os/*` e as rotas de status, verificação e OAuth de `/integrations/*` exigem o cabeçalho `X-Veratus-Admin-Token` (sem ele, 401). Os webhooks (`.../webhook`, `/integrations/shopee/push`) não usam o token: conferem a assinatura ou o token de verificação da plataforma (sem ela, 401 ou 403). Nenhuma rota devolve segredo, custo por peça ou dado pessoal de cliente. Envio e escrita externos obedecem a uma chave própria de cada canal (`render.yaml`); desligada, a rota responde com a prévia ou `BLOCKED`.

### Agentes, comandos, auditoria e escritório

| Rota | Para que serve |
|---|---|
| `GET /os/agents`, `GET /os/agents/<id>` | Os 25 papéis: nível, supervisor, permissões e estado. |
| `GET,POST /os/commands` | Ordem em linguagem natural ao gerente geral ("Prepare os relógios para os marketplaces", "Faça a análise de todos os canais"). |
| `GET /os/tasks`, `GET /os/tasks/<id>`, `GET /os/audit` | Tarefas dos agentes e o registro de auditoria. |
| `GET /os/approvals`, `POST /os/approvals/<id>/approve`, `.../reject` | Aprovações pendentes de operações sensíveis. |
| `GET /os/escritorio`, `GET /os/escritorio/painel` | O escritório virtual: cada agente como funcionário, sem dado de cliente. A página pede o token e lê a rota. |
| `GET /os/canais/analise` | Gestão, marketing, contabilidade e vendas de cada canal e o total da empresa (só leitura). |
| `POST /os/expediente` | O dia de trabalho sem ninguém apertar botão: conversas pendentes, pedidos dos marketplaces e posts vencidos a cada chamada; revisão às 8, 12 e 18 h e análise às 8 e 18 h (São Paulo). Chame de hora em hora, por exemplo um Cron Job do Render com `curl -X POST -H "X-Veratus-Admin-Token: $TOKEN" https://veratus.onrender.com/os/expediente`. Repetir na mesma hora não duplica. |
| `GET /os/status`, `GET /os/readiness`, `GET /os/flow`, `GET /os/data-quality` | Estado geral, prontidão, fluxo de vendas por canal e qualidade dos dados. |

### Produto, preço e anúncios

| Rota | Para que serve |
|---|---|
| `/os/products`, `/os/products/<sku>` (+ `/events`, `/override`, `/transition`) | Product Master: cadastro, ficha, histórico, bloqueio do fundador e mudança de estado. |
| `POST /os/pricing/calculate`, `GET /os/pricing/channel-plan/<sku>` (+ `/request`), `GET /os/economics/<sku>`, `POST /os/economics/simulate` | Preço e margem por canal com as taxas de `config/channel_fees`. |
| `GET /os/watches/readiness` | O que falta na ficha dos relógios, por modelo e por canal. |
| `POST /os/distribution/drafts/<sku>`, `GET /os/listings`, `GET /os/listings/<canal>/<sku>`, `POST .../prepare`, `.../publish`, `.../pause` | Rascunho, leitura e (com todas as chaves e o papel certos) publicação e pausa de anúncio por canal; Mercado Livre confere o resultado depois de escrever. |
| `GET /os/publish-plans`, `GET /os/sync/conflicts`, `POST /os/sync`, `POST /os/sync/mercado-livre/plan` | Planos de publicação, conflitos de preço e estoque e o plano de sincronia (só leitura). |
| `GET /os/channels`, `GET /os/channels/<canal>`, `POST /os/connections/refresh` | Estado e prontidão de cada marketplace. |
| `GET /os/meta-catalog`, `POST /os/meta-catalog/readback`, `GET /feeds/meta-catalog.csv` | Catálogo da Meta: prontidão peça a peça, leitura de volta e o feed que o Commerce Manager busca. |

### Atendimento (Harvey) e pedidos

| Rota | Para que serve |
|---|---|
| `GET /os/conversations`, `/metrics`, `/alerts`, `/readiness`, `/adapters` | Conversas e indicadores do motor de atendimento. |
| `POST /os/conversations/process` | Passa as mensagens pendentes de um canal pelo motor. |
| `GET /os/conversations/queue`, `POST /os/conversations/queue/<id>/<decisão>` | Fila de rascunhos do Harvey para aprovar ou recusar. |
| `POST /os/conversations/<canal>/<id>/state` | Passa a conversa ao fundador ou devolve ao Harvey. |
| `POST /os/conversations/<canal>/inbound` | Pergunta do comprador colada do painel (Shopee, TikTok Shop, Amazon). |
| `GET /os/harvey` | O que o Harvey aprendeu: canais do perfil e objeções. |
| `GET,POST /os/orders`, `GET /os/orders/<id>`, `POST /os/orders/<id>/status` | Orders Ledger: o pagamento só muda de estado pela confirmação do provedor. |
| `GET /os/whatsapp/health` | Só leitura na Meta: número, nome, qualidade, app assinado e modelos de mensagem. |
| `GET /os/crm/twenty`, `POST /os/crm/twenty/sync` | Funil no Twenty (prévia; envio só com a chave ligada). |

### Redes sociais e mídia paga

| Rota | Para que serve |
|---|---|
| `GET /os/social/posts`, `POST /os/social/request-approval`, `.../posts/<id>/edit`, `.../reject` | Calendário de posts e a aprovação do fundador. |
| `POST /os/social/run-due`, `POST /os/social/insights` | Publica o que venceu e mede; chave desligada, só prévia. |
| `GET /os/social/comments`, `POST /os/social/comments/<id>/<ação>/<decisão>` | Triagem de comentários e aprovação da resposta pública. |
| `/os/paid-media/*` (`status`, `accounts`, `plan`, `approve`, `execute`, `sync`, `creative-review`, `experiments`, `incidents`, `shift-report`) | Mídia paga em rascunho, com limites financeiros e aprovação humana. |

### Integrações e webhooks

| Rota | Para que serve |
|---|---|
| `GET,POST /integrations/whatsapp/webhook` | WhatsApp: verificação e mensagens. `/integrations/whatsapp/conversations` (+ `/<id>`, `/send`, `/state`) é a caixa de entrada humana; `/integrations/whatsapp/drafts/process` está aposentada (use `/os/conversations/process`). |
| `GET,POST /integrations/facebook/webhook` | Messenger da Página (canal principal). |
| `GET,POST /integrations/instagram/webhook`, `/integrations/instagram/replies`, `.../replies/process`, `/integrations/instagram/token/status`, `.../token/refresh` | Instagram Direct, resposta privada a comentário e ciclo de vida do token. |
| `POST /integrations/mercado-pago/webhook` | Confirmação de pagamento (assinatura validada e leitura de volta). |
| `/integrations/mercado-livre/*` (`oauth/start`, `oauth/callback`, `status`, `notifications`) | Mercado Livre: login da loja, status e fila de notificações. |
| `/integrations/shopee/*` (`oauth/start`, `oauth/callback/<state>`, `status`, `check`, `push`) | Shopee: login, status, checagem e push assinado. |
| `/integrations/tiktok-shop/*` (`oauth/start`, `oauth/callback`, `status`, `check`, `webhook`) | TikTok Shop: login, status, checagem e webhook assinado. |
| `/integrations/tiktok/*` (`oauth/start`, `oauth/callback`, `status`, `check`) | TikTok conteúdo: mídia aprovada para a caixa de entrada do criador. |
| `/integrations/amazon/status`, `/integrations/amazon/check` | Amazon: status e checagem. |
| `POST /integrations/<shopee\|amazon\|tiktok-shop>/orders/sync` | Importa os pedidos pagos do marketplace para o Orders Ledger. |
| `POST /integrations/chatwoot/webhook` | A resposta do fundador no Chatwoot sai pelo canal. |
