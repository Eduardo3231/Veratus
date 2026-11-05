# COMECE AQUI — Veratus OS

O Veratus OS é uma empresa virtual de 25 agentes coordenados: o Harvey vende, a equipe de produto prepara os anúncios, os agentes de marketplace cuidam de cada canal, a equipe de análise lê gestão, marketing, contabilidade e vendas de cada canal, e o gerente geral consolida tudo para o fundador. Este roteiro leva do ambiente local até a leitura do sistema. O estado de cada rodada, as decisões e o que falta estão em `docs/HANDOFF.md` (leia o topo antes de editar).

## 1. Criar o ambiente

No PowerShell, dentro da pasta do projeto:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

## 2. Criar o `.env`

```powershell
Copy-Item .env.example .env
```

Abra `.env` e preencha pelo menos:

```text
OPENAI_API_KEY=...
DATABASE_URL=postgresql://...
VERATUS_AGENT_SHARED_SECRET=...
VERATUS_ADMIN_TOKEN=...
VERATUS_SESSION_SALT=...
```

Para gerar valores aleatórios simples no PowerShell:

```powershell
[guid]::NewGuid().ToString("N")
```

Use um valor diferente para cada segredo. Não envie o `.env` para o Git ou para chats.
O serviço carrega automaticamente o `.env` local sem sobrescrever variáveis já definidas no ambiente.
Para testes puramente locais, `DATABASE_URL` pode ficar vazio e o agente usará SQLite. Staging e produção devem usar PostgreSQL.

## 3. Rodar verificações

```powershell
python -m ruff check .
python -m ruff format --check .
python -m pytest api\test_main.py tests -q
python -m veratus_agents.knowledge lint
python -m veratus_agents.knowledge eval
```

Os testes de navegador do site (`tests/test_landing_browser.py`) precisam do Playwright e de um Chromium; sem eles, pulam.

## 4. Testar o Sales Agent sem WhatsApp

```powershell
python -m veratus_agents.cli "Gostei do Ocean Blue. Qual o preço e prazo?" --customer teste-001 --product "Ocean Blue"
```

Esta chamada usa a API OpenAI e pode gerar custo. Sem `OPENAI_API_KEY` válida, o teste real fica pendente; os testes automatizados cobrem o fluxo com saídas simuladas. O resultado esperado é um rascunho Pydantic, revisão independente, gate estruturado, métricas e `run_id`, sem envio externo.

## 5. Rodar o serviço HTTP

```powershell
python -m integrations.webhook
```

Verifique:

```text
http://127.0.0.1:5000/agent/health
```

Para chamar o agente via API, leia `integrations/agent_api.md`.

## 6. Ver a empresa funcionando, sem clientes reais

```powershell
python scripts\simular_operacao.py
```

Roda um dia inteiro (conversas, pedidos, pagamentos, anúncios, redes e o expediente dos 25 agentes) com o código real e clientes fictícios. Com o serviço no ar, `GET /os/escritorio/painel` mostra o escritório virtual (cada agente trabalhando) e `GET /os/canais/analise` a análise de cada canal; ambos pedem o `X-Veratus-Admin-Token`.

## 7. Testar o Product Master

Com o serviço HTTP em execução, use as rotas `/os/*` listadas em `integrations/agent_api.md` (seção "Veratus OS e Product Master"). O Product Master funciona sem chamada a modelo e, por isso, não consome tokens. Ele cria SKU, calcula preço, aplica QA e mantém o histórico de cada decisão.

## O que está pronto, o que depende de chave e o que falta

- **Pronto e testado (com dados de teste):** os 25 agentes e as permissões, o Harvey nos canais (WhatsApp, Instagram, Messenger e marketplaces), o filtro de afirmações, o Orders Ledger, as análises por canal, o expediente, o escritório virtual e os rascunhos de anúncio nos cinco canais.
- **Pronto, mas desligado por chave:** envio ao cliente em cada canal, publicação de anúncios e posts, importação de pedidos e mídia paga. Cada chave está em `render.yaml` (`*_ENABLED`) e nasce `false`; quem liga é o fundador, ou o agente com a autonomia dada em 04/11/2026, um canal por vez e registrando no `docs/HANDOFF.md`.
- **Depende de credenciais e logins:** Meta (perfil, verificação, tokens e webhook), Mercado Livre, Shopee, TikTok Shop e Amazon (OAuth e chaves), modelos de mensagem do WhatsApp e, para o Harvey conversar com modelo de linguagem, `OPENAI_API_KEY`.
- **Falta definir:** preço e regra de preço das joias, estoque e prazo das peças sob encomenda, fatos ainda pendentes em `knowledge/fatos.yaml` (material, garantia, parcelas), e as taxas de `config/channel_fees` marcadas "a confirmar".
- **Sem agendador próprio:** o expediente (`POST /os/expediente`) precisa ser chamado de hora em hora, por exemplo por um Cron Job do Render.

## Arquivos importantes

- `veratus_agents/workflow.py` — agente e revisor;
- `veratus_agents/policy.py` — gate determinístico;
- `catalog/products.json` — catálogo usado pela IA;
- `integrations/webhook.py` — API HTTP;
- `integrations/agent_api.md` — rotas do agente e do Veratus OS;
- `veratus_agents/operations.py` — registry, comandos, tarefas, permissões e simulação do Veratus OS;
- `AGENT_REGISTRY`, em `veratus_agents/operations.py` — os 25 papéis, com nível, supervisor e permissões. Com o serviço no ar, `GET /os/agents` e `GET /os/agents/<id>` (cabeçalho `X-Veratus-Admin-Token`) devolvem o registro e o estado de cada papel;
- `veratus_agents/conversations.py`, `harvey.py`, `channel_adapters.py` — o motor de conversa do Harvey, seus modos (`SHADOW`, `SUPERVISED`, `AUTO_SAFE`, `AUTO`) e os canais;
- `veratus_agents/orders.py`, `product_master.py`, `channel_analysis.py`, `channel_economics.py` — pedidos, produtos, análise por canal e taxas;
- `veratus_agents/office.py` e `integrations/escritorio/` — o escritório virtual;
- `knowledge/` — fatos confirmados, voz, roteiro e objeções do Harvey;
- `docs/automation/` — runbooks do WhatsApp e do Instagram, Chatwoot e Twenty, e o prompt do Harvey;
- `docs/HANDOFF.md` — estado atual, decisões abertas e verificação de cada rodada;
- `docs/evidence/` — auditorias e evidências por rodada.
