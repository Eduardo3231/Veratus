# Task Contracts e revisão — rodada operacional de 25/09/2026

Nesta rodada, nenhuma ação externa foi executada: não houve deploy, publicação, campanha, envio, gasto nem escrita em API externa. As flags continuam `PUBLISH_ENABLED=false`, `PAID_MEDIA_LIVE_WRITES=false` e `PAID_MEDIA_AUTONOMY_MODE=SHADOW`.

## Onda 0 — Inventário de verdade

- **Objetivo:** estado real de cada capacidade, com evidência.
- **Escopo:** leitura do repositório, testes locais e GETs públicos sem credenciais.
- **Fora do escopo:** alterar código ou autenticar em rotas administrativas.
- **Dependências:** nenhuma.
- **Risco:** baixo.
- **Aprovação:** não se aplica.
- **Verificação:** comandos listados na tabela.
- **Evidência:** `truth-table-2026-09-25.md`.

## Onda 1A — Regressão visual da landing

- **Objetivo:** o conteúdo precisa ficar visível sem JS e sem rolagem.
- **Escopo:** `landing/styles.css`, `landing/index.html`, `landing/site.js` e dois testes novos.
- **Fora do escopo:** redesenho, novas seções e deploy.
- **Risco:** médio, porque a página é pública.
- **Aprovação:** o deploy é do fundador.
- **Verificação:**
  - `tests/test_landing_resilience.py` (estático, roda no CI);
  - `tests/test_landing_browser.py` (Playwright em 1440×900 e 390×844, com e sem `reduced-motion`, JS bloqueado e JS atrasado 6 s).
- **Evidência:** `landing-before-public-1440.jpg` e `landing-after-local-1440.jpg`.

**Reprodução.** Com Playwright no Chrome, contra a URL pública e contra o local, o `#manifesto-title` e as demais seções abaixo da dobra ficavam com `opacity: 0` sem rolagem, e a captura mostrava cabeçalho e hero com o resto vazio. Com JS bloqueado, a tela de abertura preta nunca saía (timeout de 6 s). O link "Consultar modelo" (Royal Blue) ia sem ID de produto.

**Causa raiz** (três dependências de JS ou rolagem):

1. O `@keyframes reveal-by-view` começava em `opacity: 0`, com `animation-timeline: view()` e preenchimento `both`. Tudo fora da tela fica invisível até a linha do tempo de rolagem avançar, o que não acontece em capturas, renderizadores e revisões automáticas.
2. O `.intro-gate` (camada preta fixa, `z-index: 250`) só saía quando o JS adicionava `.is-exiting`. Qualquer erro no topo do `site.js` ou atraso no seu download deixava a página preta.
3. O catálogo existia só via JS. O texto de fallback "Carregando coleção…" não tinha nenhuma chamada para ação.

**Correção** (mínima, sem redesenho):

- O reveal passou a animar só movimento (`styles.css:308`).
- A abertura ganhou saída por CSS aos 3,2 s, com o mesmo movimento da saída por JS (`styles.css:74-77`).
- O fallback do catálogo agora leva um link de WhatsApp. O caminho de erro do `fetch` também (`site.js:262`).
- O "Consultar modelo" passou a carregar `produto=royal-blue` (`site.js:77`, `index.html`).

**Resultado:**

- **Local:** 11/11 testes da landing passam.
- **URL pública** (código antigo): 6 falham e 1 é pulado (cenário de atraso, só local). Isso confirma que o teste detecta a regressão.
- **Imagens `loading="lazy"`:** continuam vazias em capturas sem rolagem. É esperado e não afeta o visitante.

## Onda 1B — Hardening de configuração

- **Objetivo:** flags fail-closed e paridade de nomes entre `render.yaml` e `.env.example`.
- **Escopo:** `render.yaml`, `.env.example`, `paid_media.py` (tetos) e `tests/test_config_parity.py`.
- **Fora do escopo:** valores secretos e deploy.
- **Risco:** baixo.
- **Aprovação:** o deploy do Blueprint é do fundador.
- **Verificação:** `test_config_parity.py`.
- **Resultado:**
  - 8 flags de canal declaradas como `"false"`;
  - `GEMINI_API_KEY` e as proteções de log do SDK adicionadas ao `.env.example`;
  - teto `0` respeitado (antes virava 20/140), negativo vira `0` e inválido volta ao padrão;
  - fator de segurança acima de 1 passa a bloquear.
- **Exceções documentadas no teste:** `PORT` e `PYTHONUNBUFFERED` só no Render; `MAILERLITE_*` só local.

## Onda 1C — Orders Ledger

- **Objetivo:** registrar uma venda real em menos de 30 s, com idempotência, auditoria e sem dados pessoais.
- **Escopo:** `veratus_agents/orders.py`, rotas `/os/orders*` e `tests/test_orders_ledger.py`.
- **Fora do escopo:** importar as duas vendas relatadas e fazer deploy.
- **Risco:** médio (dado financeiro).
- **Aprovação:** o deploy é do fundador.
- **Verificação:** 15 testes (unidade e HTTP).

A extensão foi avaliada e descartada: `marketing_performance` agrega por dia e campanha, não por pedido. O ledger reaproveita o padrão de armazenamento de `metrics.py` (SQLite local, PostgreSQL com `DATABASE_URL`).

**Rotas** (admin, `X-Veratus-Admin-Token`, limite de 8 KiB, rate limit):

- `POST /os/orders`: `201 created`, `200 duplicate`, `409` quando o mesmo `order_id` chega com outros dados, `400` com os campos inválidos sem ecoar os valores, `400 unknown_sku`.
- `GET /os/orders?status=&channel=&from=&to=&limit=`: `from` e `to` são dias de São Paulo. O `summary` é agregado sobre todo o período filtrado e a resposta traz `truncated`.
- `GET /os/orders/<id>`: pedido com a trilha de eventos.
- `POST /os/orders/<id>/status`: transição auditada por `event_id` (PENDING→PAID/CANCELED; PAID→SHIPPED/CANCELED/REFUNDED; SHIPPED→DELIVERED/REFUNDED; DELIVERED→REFUNDED).

**Proteção de dados:** os campos livres (`evidence_ref`, `recorded_by`, `utm_*`) recusam e-mail e telefone. Não existe campo para nome ou telefone do cliente.

**Registrar uma venda** (depois do deploy; token lido do ambiente):

```powershell
$h = @{ "X-Veratus-Admin-Token" = $env:VERATUS_ADMIN_TOKEN }
$venda = @{ order_id = "wa-20260925-01"; sku = "ocean-blue"; channel = "whatsapp"; visit_ref = "VT-RJ5MO2"; sale_price = "289.90"; payment_method = "pix"; status = "PAID"; evidence_ref = "comprovante-pix-pasta-vendas-01"; recorded_by = "fundador" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "https://veratus.onrender.com/os/orders" -Headers $h -ContentType "application/json" -Body $venda
```

Para vendas antigas, informe `created_at` com fuso, por exemplo `"2026-09-20T15:30:00-03:00"`.

## Onda 2 — Economics, tracking e primeiro turno em SHADOW

- **Objetivo:** economics honesto, decisão de tracking preparada e execução do comando do fundador em SHADOW.
- **Escopo:** execução local pelas rotas reais, com correções do worker vindas da revisão.
- **Fora do escopo:** qualquer integração com a Meta.
- **Risco:** baixo, porque não houve escrita externa.
- **Aprovação:** a decisão de tracking é do fundador.
- **Evidência:** `economics-2026-09-25.md`, `../decisions/ADR-paid-media-tracking.md` e `shadow-shift-2026-09-25.json`.

**Shift Report (SHADOW), conforme executado:**

| Campo | Valor |
| --- | --- |
| Roteamento do comando em linguagem natural | `400 command_routed_elsewhere` → `/os/paid-media/plan`. Antes desta rodada caía em `prepare` de catálogo Meta. |
| Saúde do worker | `BLOCKED` |
| Modo | `SHADOW`, `live_writes=false` |
| SKU | `arctic-white`. É o primeiro dos 9 elegíveis, **sem critério comercial**; o Black GMT também é elegível e não aparece na landing. |
| Hipótese | "Uma apresentação premium de um único relógio pode gerar intenção qualificada sem diluir o orçamento piloto." |
| Estrutura | 1 campanha, 1 conjunto, até 2 criativos |
| Objetivo | `BLOCKED_UNTIL_TRACKING` |
| Criativo recomendado | `NEEDS_REVISION`: faltam gancho, oferta, prova e chamada para ação |
| Economics | `INCOMPLETE`. CPA de equilíbrio, CPA-alvo e ROAS ficam `null`. Teto teórico: R$ 224,90 / R$ 157,43. |
| Teto de gasto | R$ 20,00/dia; teste máximo R$ 140,00; gasto hoje R$ 0,00 |
| Blockers | `ECONOMICS_INCOMPLETE`, `PURCHASE_TRACKING_UNVERIFIED`, `META_READBACK_NOT_CONNECTED`, `CREATIVE_NEEDS_REVISION` |
| Aprovação | nenhuma aberta (plano bloqueado). `approve` → `409 plan_has_open_blockers`. `execute` → `409` com 7 motivos. |
| Decisão | `ESCALATE` |
| Ação que precisará da sua aprovação | Depois de remover os 4 blockers: aprovar o rascunho exato da campanha Meta e o teto de R$ 20/dia (teste até R$ 140). Isso ainda exige `PAID_MEDIA_LIVE_WRITES=true`, modo diferente de SHADOW e um adaptador de escrita que não existe. |

## Onda 3 — Mercado Livre até o publish plan

- **Objetivo:** percorrer a sequência oficial sem escrita.
- **Escopo:** auditoria do OAuth, execução local e instruções ao fundador.
- **Fora do escopo:** a primeira escrita.
- **Risco:** baixo.
- **Aprovação:** credenciais e autorização são do fundador.
- **Resultado:** `AUTH_REQUIRED`.
- **Evidência:** `mercado-livre-readiness-2026-09-25.md`.

## Revisão independente (1A, 1C, 2C)

Revisão com o skill `code-review` (nível high) sobre o código novo e o worker não commitado. Formato: `arquivo:linha — severidade — problema — cenário de falha — disposição`.

1. `veratus_agents/paid_media.py:251` e `veratus_agents/operations.py:589` — **ALTA** — o `PaidMediaStore` e o `CommandEngine` gravam o snapshot inteiro numa única linha, então o último processo a gravar vence. Com `WEB_CONCURRENCY=2`, o processo B pode apagar o experimento e a aprovação criados pelo processo A, e depois `/approve` ou `/execute` respondem 404. **ABERTO.** É um problema de arquitetura anterior a esta rodada, no runtime já commitado. Correções possíveis: persistência por registro ou recarregar e gravar dentro de uma transação com lock; como paliativo, `WEB_CONCURRENCY=1`. Decisão do fundador; bloqueia o uso do worker e do runtime em produção.
2. `veratus_agents/paid_media.py:474` — **MÉDIA** — a chave fixa `paid-media-shadow-v1` devolvia para sempre o primeiro plano bloqueado. **CORRIGIDO:** sem chave explícita, o plano é identificado por uma impressão digital de SKU, blockers, economics, criativo e tetos. Teste: `test_plan_replays_until_inputs_change`.
3. `veratus_agents/paid_media.py:615` — **MÉDIA** — `execute` aceitava a aprovação de outro experimento ou de outro tipo. **CORRIGIDO** com `_bound_approval`. Teste: `test_execute_rejects_approval_from_another_experiment`.
4. `veratus_agents/paid_media.py:629` — **MÉDIA** — `approve` aprovava plano com blockers e não registrava quem aprovou. **CORRIGIDO:** plano bloqueado não abre aprovação, `approve` devolve `409 plan_has_open_blockers` e grava `resolved_by` e `resolved_at`. A rota passou a exigir `actor`.
5. `veratus_agents/paid_media.py:474` — **MÉDIA** — idempotência e experimento eram gravados antes das chamadas ao engine; uma falha deixava plano pela metade. **CORRIGIDO:** o engine roda primeiro e só depois o plano é gravado.
6. `integrations/webhook.py:889` — **MÉDIA** — o resumo de pedidos era calculado sobre a lista truncada (200). **CORRIGIDO:** agregação SQL (`OrderLedger.summary`) mais o campo `truncated`.
7. `integrations/webhook.py:889` — **MÉDIA** — os filtros de data usavam o dia em UTC (a virada acontecia às 21h em São Paulo). **CORRIGIDO** com `local_day_bounds` (UTC−3). Teste: `test_day_filters_follow_sao_paulo_calendar`.
8. `veratus_agents/operations.py:1930` — **BAIXA** — a auditoria operacional informava teto e tracking fixos no código. **CORRIGIDO:** passa a ler `PaidMediaConfig`, o tracking do adaptador e o economics de cada SKU elegível.
9. `veratus_agents/operations.py:977` — **MÉDIA** (introduzido e corrigido nesta rodada) — o roteamento de mídia paga capturava perguntas do tipo "quais… pendentes" e a auditoria. **CORRIGIDO:** o desvio agora exige verbo de planejamento e fica depois da regra de auditoria.
10. `veratus_agents/paid_media.py:187` — **BAIXA** — fator de segurança 7 (digitado no lugar de 0,7) produzia CPA-alvo acima do equilíbrio. **CORRIGIDO:** acima de 1 vira 0, o economics fica `INVALID` e o plano bloqueia.

**Achados próprios, não corrigidos:**

- `paid_media.py:491-497` — **BAIXA** — o criativo padrão se autodeclara `compliance: True` e `landing_match: True`. Não produz `READY_FOR_TEST` porque faltam gancho, oferta, prova e chamada para ação, mas o registro engana.
- `paid_media.py:450` — **MÉDIA** — o candidato é o primeiro elegível e pode ser o Black GMT, que não está na landing. Isso gera anúncio sem página correspondente.
- `integrations/meta_content_publisher.py` — **MÉDIA** — `--publish` não passa pelo `ExternalWriteGuard`.
- `veratus_agents/commercial_config.py:14` — **ALTA (dados)** — identidade do fornecedor no histórico do Git já enviado ao remoto.
