# Entrada do Sales Agent V1

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
