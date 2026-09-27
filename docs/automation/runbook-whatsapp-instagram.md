# Runbook — ativar WhatsApp Cloud API e comentário → DM no Instagram

- **Estado em 26/09/2026:** IMPLEMENTED + TESTED localmente. **Não DEPLOYED, não CONNECTED.**
- Os dois envios estão travados: `WHATSAPP_SEND_ENABLED=false` e `INSTAGRAM_DM_ENABLED=false`.
- Toda etapa marcada com ⚠️ é ação externa: exige a sua confirmação no momento em que for feita.

Secrets vão **só** no painel do Render (serviço `veratus-leads` → Environment). Nunca no chat, no Git ou em print de tela.

## Antes de tudo (decisões)

1. **Número do WhatsApp.**
   - Número novo dedicado à API (recomendado para começar): não mexe no atendimento atual.
   - Migrar o número 11 95832-3612 em modo de coexistência com o app WhatsApp Business.

   Os links do site hoje apontam para o número atual.
2. **Deploy.** Os canais só existem em produção depois do commit e deploy desta rodada e da anterior. ⚠️

## WhatsApp Cloud API

1. **Conta e app** ⚠️
   - No Meta Business Suite, confirme a verificação da empresa.
   - Em developers.facebook.com, crie um app do tipo Business, adicione o produto **WhatsApp** e vincule a conta do WhatsApp Business.
   - Comece pelo **número de teste** que a Meta fornece.
2. **Credenciais** (painel do Render):

   | Variável | De onde vem |
   | --- | --- |
   | `WHATSAPP_ACCESS_TOKEN` | token de **usuário do sistema** com `whatsapp_business_messaging` (não use token temporário do painel) |
   | `WHATSAPP_PHONE_NUMBER_ID` | WhatsApp → API Setup → Phone number ID |
   | `WHATSAPP_APP_SECRET` | App → Configurações → Básico → Chave secreta |
   | `WHATSAPP_VERIFY_TOKEN` | um valor aleatório **exclusivo** (`python -c "import secrets; print(secrets.token_urlsafe(24))"`). A Meta o envia na URL, então ele aparece em logs de acesso. Nunca reaproveite outro segredo aqui. |
   | `VERATUS_SESSION_SALT` e `VERATUS_TOKEN_ENCRYPTION_KEY` | já exigidas pelo Sales Agent e pelo OAuth. Se faltarem, o webhook responde 503 e a Meta reenvia por até 7 dias. |

3. **Webhook** (App → WhatsApp → Configuration) ⚠️
   - Callback URL: `https://veratus.onrender.com/integrations/whatsapp/webhook`
   - Verify token: o mesmo de `WHATSAPP_VERIFY_TOKEN`
   - Assine o campo **messages**.
4. **Teste sem enviar nada** (flag ainda em `false`), com o token de admin lido do ambiente:

   ```powershell
   $h = @{ "X-Veratus-Admin-Token" = $env:VERATUS_ADMIN_TOKEN }
   $api = "https://veratus.onrender.com"
   # 1) Mande "oi" do seu celular para o número de teste. Depois:
   Invoke-RestMethod "$api/integrations/whatsapp/conversations" -Headers $h
   # 2) Gere o rascunho com o Sales Agent (usa a chave OpenAI, tem custo):
   Invoke-RestMethod -Method Post "$api/integrations/whatsapp/drafts/process" -Headers $h -ContentType "application/json" -Body "{}"
   # 3) Aprove o texto final do run em /agent/runs/<run_id>/decision (fluxo já existente).
   # 4) Tente enviar: com a flag em false a resposta é 409 com a prévia e os motivos.
   $send = @{ actor = "fundador"; run_id = "<run_id>" } | ConvertTo-Json
   Invoke-RestMethod -Method Post "$api/integrations/whatsapp/conversations/<conversation_id>/send" -Headers $h -ContentType "application/json" -Body $send
   ```

5. **Primeiro envio real** ⚠️: mude `WHATSAPP_SEND_ENABLED` para `true` no Render, ainda com o número de teste. Repita o passo 4.4, confirme no celular e confira `delivery_status` em `GET .../conversations/<id>`.

**Regras que o código garante:**

- Só sai resposta aprovada por você (`run_id` aprovado **desta** conversa) ou texto escrito por uma pessoa (com `idempotency_key`).
- Nada sai fora da janela de 24 h desde a última mensagem do cliente. Fora dela, a Meta só aceita template aprovado, e templates ainda não estão implementados.
- "Atendente", "humano" ou "pessoa real" passam a conversa para `HUMAN` e o agente para. Para devolver: `POST .../conversations/<id>/state` com `{"state":"BOT","actor":"fundador"}`.
- O número do cliente fica cifrado. Listagens mostram só `***` + 4 dígitos.

**Registrar a venda que veio da conversa:** no `POST /os/orders`, inclua `"conversation_ref": "<conversation_id>"` e o `visit_ref` da conversa.

## Instagram: comentário → resposta privada

1. **Conta e app** ⚠️
   - Conta profissional do Instagram da Veratus.
   - App com **Instagram API with Instagram Login** e permissões `instagram_business_basic` e `instagram_business_manage_comments`, conforme a página oficial de Private Replies. Confira no painel se o app também pede a permissão de mensagens.
   - Para funcionar com qualquer pessoa, e não só com contas de teste do app, a Meta pode exigir **App Review / acesso avançado**.
2. **Credenciais** (Render):

   | Variável | Valor |
   | --- | --- |
   | `INSTAGRAM_ACCESS_TOKEN` | token de longa duração. **Expira em cerca de 60 dias**; a renovação ainda não é automática, então anote a data. |
   | `INSTAGRAM_USER_ID` | ID da conta profissional |
   | `INSTAGRAM_APP_SECRET` | chave secreta do app do Instagram |
   | `INSTAGRAM_VERIFY_TOKEN` | valor aleatório seu |

3. **Webhook** ⚠️: `https://veratus.onrender.com/integrations/instagram/webhook`, assinando o campo **comments**.
4. **Teste:**
   - Comente "coleção" num post. `GET /integrations/instagram/replies` deve mostrar o comentário como `PENDING`.
   - `POST /integrations/instagram/replies/process` com a flag em `false` devolve a **prévia** do texto e do link.
   - Com `INSTAGRAM_DM_ENABLED=true` ⚠️, o mesmo comando envia, e o status vira `SENT`.
5. **Rotina:** o processamento é acionado por essa rota. Depois de validado, ela pode rodar a cada poucos minutos por um cron job do Render (outra ação externa).

**Regras garantidas:** uma resposta por comentário, até 7 dias; comentários sem palavra-chave não são nem armazenados; a própria conta é ignorada; nada identifica quem comentou.

Palavras-chave e texto: `veratus_agents/instagram_comment_rules.json` (hoje: "coleção", "catálogo", "link").

## Não usar

DM para quem nunca falou com a conta. O antigo envio em massa por CSV foi apagado em 26/09/2026 porque viola a política de mensagens do Instagram, que só permite responder a quem escreveu ou comentou. `tests/test_no_cold_outreach.py` falha se esse envio voltar.

## Documentação oficial consultada (26/09/2026)

- [WhatsApp — Create a webhook endpoint](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/create-webhook-endpoint/)
- [WhatsApp — Service messages e janela de 24 h](https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/send-messages)
- [Instagram — Private Replies](https://developers.facebook.com/documentation/instagram-platform/private-replies)
