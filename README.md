# Veratus — base operacional

Este repositório concentra a landing, o catálogo, a captação e a primeira base local de agentes da Veratus. Comece por [START_HERE.md](START_HERE.md).

## Estrutura principal

- [landing/index.html](landing/index.html) — landing premium com consulta pelo WhatsApp.
- [landing/styles.css](landing/styles.css) — visual refinado em preto e dourado.
- [catalog/products.json](catalog/products.json) — Product Master com 18 itens: nove relógios e nove peças da coleção feminina. Os itens femininos permanecem em `DRAFT` até validação de material, custo e preço.
- [api/main.py](api/main.py) — API local de consulta ao catálogo em FastAPI.
- [veratus_agents/workflow.py](veratus_agents/workflow.py) — Sales Agent, revisor e gate, sem envio externo.
- [integrations/webhook.py](integrations/webhook.py) — webhook de captação com validação e rate limit; não inscreve contatos em serviços externos.
- [render.yaml](render.yaml) — configuração do deploy em Render.
- [branding/guide.md](branding/guide.md) — guia de identidade da marca.
- [marketing/influencer_prospection.md](marketing/influencer_prospection.md) — processo de prospecção de microinfluenciadores.
- [automations/property-outreach.md](automations/property-outreach.md) — fila de rascunhos para oferta de vídeos de imóveis.
- [marketing/commission_model.md](marketing/commission_model.md) — regra de comissão e ROI.
- [requirements/requirements_map.md](requirements/requirements_map.md) — mapa de requisitos de negócio e operação.

## Fluxo atual

1. O visitante alterna entre oito relógios e nove peças femininas na vitrine e inicia uma conversa no WhatsApp pela landing. A exibição feminina é uma prévia editorial e não representa aprovação comercial.
2. Uma mensagem comercial pode ser enviada manualmente ou por um futuro adaptador oficial ao Sales Agent.
3. O Sales Agent consulta o catálogo, prepara um rascunho e passa por revisor e gate determinístico.
4. O run fica registrado para decisão humana. Aprovação interna não envia mensagem ao cliente.

Com `DATABASE_URL`, memória e runs usam PostgreSQL; SQLite é reservado ao desenvolvimento. O `render.yaml` inclui um banco de staging, mas nenhum recurso foi criado ou publicado nesta rodada.

O webhook de leads e o importador manual do MailerLite continuam no projeto, mas a landing atual não envia formulário para eles. O importador exige `consent=true` e a fonte dessa autorização em cada linha revisada. O MailerLite usa API Classic v2; confirme o tipo de conta antes de qualquer migração.

Para a nova oferta de vídeos de imóveis, o módulo [automations/property_outreach.py](automations/property_outreach.py) gera mensagens personalizadas e horários sugeridos de duas em duas horas. A fila começa em `pending_review`; não há scraping nem envio automático.

## Primeira API de produtos

A API de consulta fica isolada em `api/` e usa o Product Master compartilhado de 18 itens. Ela expõe:

- `GET /health`
- `GET /api/catalog`
- `GET /produtos`
- `GET /produtos/{id}`

Para executar localmente:

```powershell
.\.venv\Scripts\python.exe -m pip install -r api\requirements.txt
.\.venv\Scripts\python.exe -m uvicorn api.main:app --reload --port 8000
```

Leia [api/README.md](api/README.md) para executar seus testes. Os endpoints do agente ficam no serviço Flask em `integrations/webhook.py`; leia [integrations/agent_api.md](integrations/agent_api.md). A API FastAPI ainda não está conectada ao Render.

## Verificação local

Use este comando para validar o comportamento principal:

```powershell
cd "c:\Users\PC GAMER\Teste"
.\.venv\Scripts\python.exe -m compileall integrations
.\.venv\Scripts\python.exe -c "from integrations.webhook import app; client = app.test_client(); print(client.get('/health').status_code); print(client.post('/webhook', json={'email':'teste@veratus.local','whatsapp':'11999999999','source':'local_test'}).status_code)"
```

## Variáveis de ambiente

Copie [.env.example](.env.example) para um arquivo real .env e preencha somente o que for necessário.

## Observações

- Não publique campanhas de anúncio sem revisão final do texto, valor e canal de atendimento.
- Mantenha o WhatsApp da venda como canal principal antes da confirmação do pagamento.
- O Sales Agent foi verificado localmente com mocks. Uma execução real precisa de chave OpenAI; WhatsApp bidirecional e envio continuam ausentes. O Blueprint já aponta runs e memória para PostgreSQL, mas essa conexão ainda precisa ser validada em staging.
