# Veratus — o sistema operacional da empresa

A Veratus vende relógios e, agora, correntes e joias de revenda. Este repositório guarda o site, o catálogo e o **Veratus OS**: uma empresa virtual de 25 agentes que atende clientes, prepara anúncios, acompanha pedidos, analisa cada canal e entrega o relatório ao fundador. Comece por [START_HERE.md](START_HERE.md) e, antes de editar, leia o topo de [docs/HANDOFF.md](docs/HANDOFF.md) (o diário das rodadas e das decisões) e as regras em [CLAUDE.md](CLAUDE.md) e [AGENTS.md](AGENTS.md).

## O que o sistema faz

- **Harvey**, o agente de vendas, atende no WhatsApp, Instagram, Messenger e marketplaces. Apresenta-se como assistente virtual, usa só fatos confirmados e chama o fundador quando o cliente pede uma pessoa ou ameaça ação jurídica.
- **25 agentes** em três níveis (gerente geral, supervisores e agentes) cobrem produto, marketplaces, atendimento, redes e mídia, e análise. Cada um tem função, supervisor e permissões em `AGENT_REGISTRY` (`veratus_agents/operations.py`); nenhum recebe a permissão de gastar ou publicar por conta própria sem passar pelo guarda de escrita externa.
- **Análise por canal** (`veratus_agents/channel_analysis.py`): gestão, marketing, contabilidade e vendas de Facebook, WhatsApp, Instagram, Mercado Livre, Shopee, TikTok Shop e Amazon, com os números reais do sistema.
- **Expediente** (`POST /os/expediente`): conversas, pedidos dos marketplaces e posts a cada chamada, revisão da empresa e análise nas horas marcadas.
- **Escritório virtual** (`/os/escritorio/painel`): cada agente como funcionário, trabalhando, esperando ou bloqueado.
- **Orders Ledger** e **Product Master**: pedidos, pagamentos (confirmados só pelo provedor) e produtos, com auditoria de cada ação. Custo, fornecedor e dado de cliente ficam no banco, nunca no Git.

## Estrutura principal

- [veratus_agents/](veratus_agents/) — o código dos agentes: [operations.py](veratus_agents/operations.py) (registro, comandos, tarefas), [conversations.py](veratus_agents/conversations.py) e [harvey.py](veratus_agents/harvey.py) (atendimento), [channel_adapters.py](veratus_agents/channel_adapters.py) (canais), [orders.py](veratus_agents/orders.py), [marketplace_ops.py](veratus_agents/marketplace_ops.py), [social_scheduler.py](veratus_agents/social_scheduler.py), [paid_media.py](veratus_agents/paid_media.py), [channel_analysis.py](veratus_agents/channel_analysis.py), [office.py](veratus_agents/office.py), [policy.py](veratus_agents/policy.py) (o filtro de afirmações).
- [integrations/webhook.py](integrations/webhook.py) — o serviço Flask: webhooks das plataformas e as rotas `/os/*`. O mapa completo está em [integrations/agent_api.md](integrations/agent_api.md).
- [knowledge/](knowledge/) — fatos confirmados, voz, roteiro de venda e objeções do Harvey.
- [catalog/products.json](catalog/products.json) — o Product Master público: 18 itens, nove relógios e nove peças femininas em `DRAFT` (sem preço até o fundador definir). Correntes e joias de revenda entram por [scripts/importar_joias.py](scripts/importar_joias.py).
- [landing/](landing/) — o site em preto e dourado, com consulta pelo WhatsApp (não publicado nesta fase). [scripts/build_landing.py](scripts/build_landing.py) gera a página a partir do catálogo.
- [api/](api/) — API de consulta ao catálogo em FastAPI, isolada; ainda não conectada ao Render.
- [config/](config/) — taxas dos canais e modelos de mensagem do WhatsApp (todos em rascunho).
- [scripts/simular_operacao.py](scripts/simular_operacao.py) — um dia inteiro de operação com o código real e clientes fictícios.
- [social/](social/), [video/correntes/](video/correntes/), [criativos/correntes/](criativos/correntes/) — calendário de posts, o vídeo e os criativos das correntes.
- [docs/](docs/) — [HANDOFF.md](docs/HANDOFF.md), runbooks em [docs/automation/](docs/automation/) (WhatsApp, Instagram, Chatwoot, Twenty e o prompt do Harvey) e evidências por rodada.
- [render.yaml](render.yaml) e [.env.example](.env.example) — o deploy no Render e todas as variáveis, uma chave `*_ENABLED` por integração, sempre nascendo desligada.
- [branding/](branding/) — guia da marca (paleta, símbolo V com trigo) e kit de perfil.
- [automations/](automations/) e [launch/](launch/) — fluxos de tráfego pago, prospecção e checklists de lançamento.
- [quarantine/](quarantine/) — mídia com marca de terceiros: não é usada.

## Como o atendimento funciona

1. A mensagem chega por um webhook da plataforma, que confere a assinatura antes de qualquer coisa.
2. O motor de conversa lê a intenção e o modo do canal decide o que fazer com a resposta: `SHADOW` (só rascunho), `SUPERVISED` (espera aprovação), `AUTO_SAFE` (envia só o de baixo risco) ou `AUTO`.
3. O filtro de afirmações barra o que não é fato confirmado: material, garantia, número de parcelas, urgência e escassez.
4. O pedido entra no Orders Ledger e só vira pago quando o provedor de pagamento confirma.

## Verificação local

```powershell
python -m pip install -r requirements-dev.txt
python -m ruff check .
python -m ruff format --check .
python -m pytest -q
python scripts\simular_operacao.py
```

Com `DATABASE_URL`, memória e pedidos usam PostgreSQL; sem ele, SQLite (desenvolvimento). O passo a passo do ambiente está em [START_HERE.md](START_HERE.md).

## Segurança

- Segredo só em variável de ambiente, declarada em `.env.example` e `render.yaml` (`sync: false`). Nunca em código, teste, log ou documento.
- O repositório é público: nada de custo por peça, margem, fornecedor ou dado de cliente.
- Rotas `/os/*` exigem o cabeçalho `X-Veratus-Admin-Token`; webhooks conferem a assinatura da plataforma.
- Anúncio pago e envio a clientes seguem as regras de [CLAUDE.md](CLAUDE.md) e [AGENTS.md](AGENTS.md).
