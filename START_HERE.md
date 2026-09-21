# COMECE AQUI — Veratus OS + Sales Agent V1

Este repositório contém a primeira implementação local do Sales Agent da Veratus. Siga esta ordem a partir da raiz do projeto.

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
python -m pytest api\test_main.py tests -q
```

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

## 6. Testar o Product Master

Com o serviço HTTP em execução, use os contratos em `docs/veratus-os.md`. O Product Master funciona sem chamada a modelo e, por isso, não consome tokens. Ele cria SKU, calcula preço, aplica QA e mantém o histórico de cada decisão.

## O que ainda não está automático

- receber mensagens reais da API oficial do WhatsApp;
- enviar respostas ao WhatsApp;
- validar, em uma instância real do Render, a persistência PostgreSQL já configurada no Blueprint;
- publicar em Ads, marketplaces ou estoque externo por agente.

Esses itens foram deixados fora do V1 de propósito. Primeiro valide a qualidade do Sales Agent com mensagens simuladas e revisão humana.

## Arquivos importantes

- `veratus_agents/workflow.py` — agente e revisor;
- `veratus_agents/policy.py` — gate determinístico;
- `catalog/products.json` — catálogo usado pela IA;
- `integrations/webhook.py` — API HTTP;
- `docs/agent-system-v1.md` — arquitetura;
- `docs/veratus-os.md` — Product Master, papéis e contratos;
- `docs/agent-registry.json` — estado e permissões das 13 funções;
- `docs/project-audit-2026-09-18.md` — auditoria atual e pendências.
- `veratus_agents/operations.py` — registry, comandos, tarefas, permissões e simulação do Veratus OS.
- `IMPLEMENTATION_REPORT.md` — resultado desta rodada e testes.
