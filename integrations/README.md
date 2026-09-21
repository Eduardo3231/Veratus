# Integrações locais — Webhook de leads

## Veratus OS — conexões read-only de marketplaces

O runtime usa `Marketplace Agent → MarketplaceConnectionService → API Client → MarketplaceStore → Sync Monitor → General Manager`.

```text
POST /os/connections/refresh
GET  /os/readiness
GET  /os/publish-plans
GET  /os/sync/conflicts
```

O refresh executa somente AUTH → ACCOUNT → CATEGORY → ATTRIBUTES → LISTING LOOKUP → READINESS. Com credenciais ausentes, registra `MISSING` sem tentar a rede. Com contrato não validado, registra `API_CONTRACT_UNVERIFIED`. Nenhuma dessas rotas escreve em marketplace.

Configure secrets somente em `.env` local ignorado pelo Git, Render Environment ou secret manager. Os nomes ficam em `.env.example`; valores não podem entrar em arquivos versionados, logs ou auditoria. Nesta fase mantenha `MARKETPLACE_MODE=LOCAL` e `PUBLISH_ENABLED=false`.

Mercado Livre possui client read-only para identidade da conta, preditor de categoria, atributos e busca de anúncio por SKU. TikTok Shop possui assinatura, lojas autorizadas, categorias e regras. Shopee e Meta permanecem bloqueados até validação do contrato regional e do escopo da conta.

### Mercado Livre — OAuth e notificações

O backend público usa duas URLs distintas:

```text
MERCADO_LIVRE_REDIRECT_URI=https://veratus.onrender.com/integrations/mercado-livre/oauth/callback
MERCADO_LIVRE_NOTIFICATION_URL=https://veratus.onrender.com/integrations/mercado-livre/notifications
```

Rotas implementadas:

```text
GET  /integrations/mercado-livre/oauth/start
GET  /integrations/mercado-livre/oauth/callback
GET  /integrations/mercado-livre/status
GET  /integrations/mercado-livre/notifications
POST /integrations/mercado-livre/notifications
```

`oauth/start` e `status` exigem `X-Veratus-Admin-Token`. O callback valida e consome `state` uma única vez, troca o código por tokens, armazena o conjunto criptografado no PostgreSQL e executa a verificação read-only. O handler de notificações valida a origem pela lista oficial configurada em `MERCADO_LIVRE_NOTIFICATION_IP_ALLOWLIST`, valida aplicação e conta, grava cada evento com idempotência e o deixa como `PENDING_SYNC_MONITOR`, sem trabalho remoto dentro da requisição.

Além das credenciais da aplicação e de `DATABASE_URL`, configure `VERATUS_TOKEN_ENCRYPTION_KEY` com uma chave Fernet persistente. A chave não pode ser trocada enquanto houver tokens armazenados sem antes executar uma migração segura. `PUBLISH_ENABLED` e `MERCADO_LIVRE_PUBLISH_ENABLED` devem permanecer `false`.

1) Na raiz do repositório, instale as dependências:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r integrations/requirements.txt
```

2) Execute o webhook local:

```powershell
python -m integrations.webhook
```

3) O serviço expõe `/health`, o `/webhook` de leads e os endpoints protegidos `/agent/*`. A landing atual direciona para WhatsApp e não chama `/webhook`.

4) Para enviar leads a uma planilha ou CRM, use uma integração consentida e testada; não presuma que a landing atual esteja captando leads pelo webhook.

5) Sincronização revisada com MailerLite
- O webhook público apenas coloca o lead na fila local. Ele não cria assinantes automaticamente.
- Revise a autorização, copie `marketing/mailing-authorized-template.csv` e mantenha `consent=true` acompanhado de `consent_source` somente quando houver registro válido.
- Confira sem transmitir dados:

```powershell
python -m integrations.push_to_mailerlite --input marketing/mailing-authorized.csv --dry-run
```

Depois configure `MAILERLITE_API_KEY` no ambiente e repita sem `--dry-run`. `MAILERLITE_GROUP_ID` é opcional. O script não reativa assinantes que cancelaram a inscrição.

O código MailerLite atual usa `api.mailerlite.com/api/v2` com `X-MailerLite-ApiKey`, que corresponde à API Classic. Confirme se a conta é Classic antes de qualquer migração. Para o Sales Agent, veja [agent_api.md](agent_api.md). O Dockerfile parte da raiz do repositório, instala `integrations/requirements.txt` e inicia `integrations.webhook:app`; o deploy real ainda precisa ser verificado.

O Blueprint define PostgreSQL e injeta `DATABASE_URL`. Sem essa variável, o agente aceita SQLite apenas no desenvolvimento local e `/agent/health` permanece `not_configured` para operação. Os segredos de entrada, administração e sessão precisam ser diferentes.
