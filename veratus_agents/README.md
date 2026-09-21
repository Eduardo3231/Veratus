# Veratus Agents — Sales Agent V1

Primeira implementação agentica da Veratus. O fluxo usa o OpenAI Agents SDK e foi desenhado para separar raciocínio, ferramentas, memória, revisão e aprovação humana.

## Fluxo

1. Uma mensagem comercial normalizada chega ao endpoint protegido `/agent/sales/draft` ou à CLI.
2. O Sales Agent consulta apenas ferramentas controladas de catálogo e política comercial.
3. O agente produz um rascunho estruturado; ele não envia nada ao cliente.
4. Um segundo agente, independente, revisa o rascunho.
5. O resultado fica em `pending_review` ou `needs_revision` no SQLite operacional.
6. Uma pessoa pode registrar `approved` ou `rejected` pelo endpoint administrativo.

Em staging/produção, histórico e runs usam PostgreSQL quando `DATABASE_URL` está presente. O modo SQLite existe apenas para desenvolvimento local. O identificador da sessão é um HMAC do identificador interno do cliente com `VERATUS_SESSION_SALT` obrigatório. Padrões comuns de e-mail e telefone presentes na mensagem são omitidos antes do registro e do envio ao modelo.

## Execução local

```powershell
pip install -r integrations\requirements.txt
$env:OPENAI_API_KEY="..."
$env:VERATUS_AGENT_SHARED_SECRET="segredo-local"
$env:VERATUS_ADMIN_TOKEN="segredo-admin"
python -m veratus_agents.cli "Gostei do Ocean Blue. Quanto custa?" --customer demo-001
```

O agente deve responder que preço precisa ser confirmado, porque o catálogo atual não contém preço validado.

## Variáveis

- `OPENAI_API_KEY`: obrigatória para o agente.
- `VERATUS_AGENT_MODEL`: modelo, padrão `gpt-4.1-mini`.
- `VERATUS_AGENT_SHARED_SECRET`: protege o endpoint de entrada do agente.
- `VERATUS_ADMIN_TOKEN`: protege leitura e decisão dos runs.
- `VERATUS_SESSION_SALT`: sal para IDs de sessão anonimizados.
- `VERATUS_AGENT_RUNTIME_DIR`: diretório dos SQLite locais.
- `VERATUS_AGENT_TRACE_SENSITIVE_DATA`: padrão `false`.
- `VERATUS_AGENT_MAX_TURNS`: padrão `8`.
- `DATABASE_URL`: obrigatório para ambiente durável; ativa `PostgresRunStore` e `PostgresSession`.
- `VERATUS_AGENT_MAX_OUTPUT_TOKENS`, `VERATUS_AGENT_MAX_TOTAL_TOKENS`, timeouts, retries e limite do histórico controlam custo e duração.

O código usa `openai-agents==0.22.2`. O fluxo foi verificado localmente com testes e mocks; uma execução real da API depende de `OPENAI_API_KEY` válida. A aprovação humana é imutável: só pode ocorrer uma vez em um run `pending_review`.

## Limite intencional do V1

Não existe ferramenta de envio de WhatsApp nesta versão. O sistema prepara, revisa e registra respostas. O envio real será uma etapa posterior, conectada à API oficial escolhida e protegida por aprovação humana.
