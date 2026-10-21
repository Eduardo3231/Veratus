# Runbook — ativar WhatsApp Cloud API e comentário → DM no Instagram

- **Estado em 18/10/2026:** IMPLEMENTED + TESTED localmente. **Não DEPLOYED, não CONNECTED.**
- Tudo travado: `WHATSAPP_SEND_ENABLED`, `WHATSAPP_TEMPLATES_ENABLED`, `WHATSAPP_READ_RECEIPTS_ENABLED`, `WHATSAPP_INTERACTIVE_ENABLED`, `WHATSAPP_MEDIA_ENABLED`, `FOUNDER_ALERT_WHATSAPP_ENABLED` e `INSTAGRAM_DM_ENABLED` em `false`; `CONVERSATION_MODE_WHATSAPP=SHADOW`.
- Toda etapa marcada com ⚠️ é ação externa: exige a sua confirmação no momento em que for feita.

Secrets vão **só** no painel do Render (serviço `veratus-leads` → Environment). Nunca no chat, no Git ou em print de tela.

## WhatsApp: o número 11 95832-3612

### 1. Decisão do caminho do número ⚠️ (só o fundador)

O número `5511958323612` já está no site e no Harvey (`harvey.WHATSAPP_NUMBER`). Confira no WhatsApp Manager, na hora, se ele é elegível.

| | Coexistência (recomendada) | Migração completa |
| --- | --- | --- |
| Celular | O app WhatsApp Business continua atendendo: é a sua caixa humana. | O app para de funcionar no número. Você atende só pelas rotas `/integrations/whatsapp/*` e `/os/*`. |
| Como entra | Embedded Signup (app com status de Tech Provider, ou um BSP que ofereça coexistência e exponha a Cloud API crua). | Apagar a conta do número no app e registrar na Cloud API. |
| Histórico | Pode ser importado (campo `history`); o código responde 200 e **não** trata como mensagem nova. | Não vai para a API. |
| Cuidados | Abrir o app atualizado com frequência (cerca de 14 dias). Alguns recursos do app são desligados, como listas de transmissão. | Sem volta fácil para o app. |

- Se o número hoje for WhatsApp **pessoal**, passe antes para o WhatsApp Business.
- **Coexistência:** no Embedded Signup, conecte o número com o app WhatsApp Business e assine também os campos `smb_message_echoes` e `history`. Quando você responde pelo celular, o eco chega em `smb_message_echoes`: o código grava a mensagem como `founder-app` e põe a conversa em `HUMAN` no canal e no motor, e o Harvey para. A forma exata do eco se confere com um envio de teste na hora de ligar.
- **Migração:** apague a conta no app e aceite atender só pela API.

### 2. Configuração na Meta ⚠️ (só o fundador)

1. **Portfólio empresarial verificado** (CNPJ e site com o mesmo nome). Sem a verificação, o limite de templates por dia é baixo e não há selo; responder a quem escreveu não depende disso.
2. **WABA e número**, com o nome de exibição **Veratus** (igual ao rodapé do site). Aguarde a aprovação do nome: uma rejeição restringe o envio.
3. **PIN de verificação em duas etapas** e registro do número: `POST /{PHONE_NUMBER_ID}/register` com `messaging_product=whatsapp` e o PIN.
4. **App Business** com o produto WhatsApp e um **usuário do sistema admin**, com token **sem validade**, permissões `whatsapp_business_messaging` e `whatsapp_business_management`, e a WABA e o app atribuídos a ele. Não use o token temporário do painel.
5. **Forma de pagamento na WABA**: template é cobrado por mensagem; resposta dentro da janela de 24 h é grátis.
6. **Templates** (WhatsApp Manager → Modelos de mensagem): envie os 3 de `config/whatsapp_templates.json` como **UTILITY**, idioma **pt_BR**, com os exemplos do arquivo. Troque `status` para `APPROVED` no arquivo só depois da aprovação. Se a Meta reclassificar algum como MARKETING, não use (o código bloqueia com `TEMPLATE_RECLASSIFIED`).

   | Nome | Texto |
   | --- | --- |
   | `pedido_confirmado` | O pagamento do pedido {{1}} foi confirmado pelo Mercado Pago. Seu pedido entra em preparação e o rastreio segue por aqui. |
   | `pedido_enviado` | Seu pedido {{1}} foi despachado. Código de rastreio: {{2}}. Qualquer dúvida, é só responder. |
   | `alerta_fundador` | Atendimento Veratus: {{1}}. {{2}} Sugestão: {{3}}. Veja no painel. |

7. **Catálogo** (opcional, para foto e produto na conversa): ligue o catálogo (`META_CATALOG_ID`) ao número no Commerce Manager, depois de ter fotos reais.

### 3. Render ⚠️ (só o fundador)

| Variável | De onde vem |
| --- | --- |
| `WHATSAPP_ACCESS_TOKEN` | token do usuário do sistema (passo 2.4) |
| `WHATSAPP_PHONE_NUMBER_ID` | WhatsApp → API Setup → Phone number ID. Com ele definido, mensagens de outro número do mesmo app (o de teste da Meta) ficam de fora das conversas reais. |
| `WHATSAPP_BUSINESS_ACCOUNT_ID` | id da WABA (usado só pela rota de saúde) |
| `WHATSAPP_APP_SECRET` | App → Configurações → Básico → Chave secreta |
| `WHATSAPP_VERIFY_TOKEN` | um valor aleatório **exclusivo** (`python -c "import secrets; print(secrets.token_urlsafe(24))"`). A Meta o envia na URL, então ele aparece em logs de acesso. Nunca reaproveite outro segredo aqui. |
| `VERATUS_SESSION_SALT` e `VERATUS_TOKEN_ENCRYPTION_KEY` | já exigidas pelo Sales Agent e pelo OAuth. Se faltarem, o webhook responde 503 e a Meta reenvia por até 7 dias. |
| `FOUNDER_ALERT_WHATSAPP` | o seu número **pessoal**, diferente de 5511958323612 (a empresa não manda mensagem para si mesma; com o número comercial, o alerta vai por e-mail). |

### 4. Deploy e webhook ⚠️

1. Deploy (as rotas só existem em produção depois do merge e do deploy).
2. App → WhatsApp → Configuration: callback `https://veratus.onrender.com/integrations/whatsapp/webhook`, verify token igual a `WHATSAPP_VERIFY_TOKEN`, campos **messages** e **message_template_status_update** (e `smb_message_echoes` e `history` na coexistência).
3. Confira se o app aparece em `GET /{WABA_ID}/subscribed_apps`; a rota de saúde abaixo faz isso.

### 5. Conferir sem enviar nada

```powershell
$h = @{ "X-Veratus-Admin-Token" = $env:VERATUS_ADMIN_TOKEN }
$api = "https://veratus.onrender.com"
# 1) Saúde da conta (só leitura na Meta): número = 5511958323612, nome aprovado,
#    qualidade, app assinado e os 3 templates APPROVED/UTILITY.
Invoke-RestMethod "$api/os/whatsapp/health" -Headers $h
# 2) Mande "oi" de outro celular para o número. Depois:
Invoke-RestMethod "$api/integrations/whatsapp/conversations" -Headers $h
# 3) O motor processa (em SHADOW, só rascunho na fila):
Invoke-RestMethod -Method Post "$api/os/conversations/process" -Headers $h -ContentType "application/json" -Body '{"channel":"whatsapp"}'
Invoke-RestMethod "$api/os/conversations/queue" -Headers $h
# 4) Prontidão para subir de nível:
Invoke-RestMethod "$api/os/conversations/readiness" -Headers $h
```

A rota antiga `POST /integrations/whatsapp/drafts/process` foi aposentada (responde 410): ela consumia as mesmas mensagens do motor.

### 6. Ligar, um passo de cada vez ⚠️ (cada um com a sua confirmação)

1. `WHATSAPP_SEND_ENABLED=true` e o próximo nível em `CONVERSATION_MODE_WHATSAPP` (`SUPERVISED`), só depois da prontidão.
2. `FOUNDER_ALERT_WHATSAPP_ENABLED=true` (pode vir antes do item 1: o alerta ao fundador não depende do envio a clientes), depois que `alerta_fundador` estiver APPROVED.
3. `WHATSAPP_TEMPLATES_ENABLED=true`, depois dos templates APPROVED no arquivo: aviso de pagamento fora das 24 h, só para conversa com pedido vinculado.
4. `WHATSAPP_READ_RECEIPTS_ENABLED=true`: "lida" e "digitando…" (nunca em SHADOW).
5. `WHATSAPP_INTERACTIVE_ENABLED=true`: link do Mercado Pago com botão, botões e lista. A forma de `cta_url` se confere com um envio de teste.
6. `WHATSAPP_MEDIA_ENABLED=true`: foto de `landing/assets` fora da quarentena e produto do feed elegível, só com fotos reais.

**Regras que o código garante:**

- Dentro da janela de 24 h desde a última mensagem do cliente: texto, botões, imagem ou produto. Fora dela, só os templates de utilidade aprovados, para conversa existente e com pedido vinculado. Os parâmetros saem sem quebra de linha, tab ou espaços repetidos.
- Áudio, foto, vídeo, documento, figurinha e localização recebem texto fixo pedindo a mensagem escrita (localização: o endereço por escrito). Foto depois do link de pagamento segue o fluxo do comprovante: nunca confirma pagamento.
- Status `failed` da Meta (por exemplo 131047, fora da janela; 131026, não entregue) fica registrado e abre a tarefa `ERRO_ENVIO` no painel, sem ping.
- "parar", "sair", "não quero receber": a saída fica registrada (`wa_optins.revoked_at`), a mensagem não vai ao motor e a confirmação sai pelas travas de sempre. Marketing só com opt-in registrado e nunca por template de utilidade.
- Um pedido de pessoa ("quero falar com um atendente", "me liga", "pessoa real"; não a palavra solta nem "não quero falar com atendente") passa a conversa para `HUMAN` no canal e no motor, e o agente para. Para devolver: `POST .../conversations/<id>/state` com `{"state":"BOT","actor":"fundador"}`; essa rota (e `POST /os/conversations/whatsapp/<id>/state`) devolve os dois juntos.
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

## Documentação oficial consultada (26/09/2026; coexistência, interativos e status a conferir de novo na hora de ligar)

- [WhatsApp — Create a webhook endpoint](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/create-webhook-endpoint/)
- [WhatsApp — Service messages e janela de 24 h](https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/send-messages)
- [Instagram — Private Replies](https://developers.facebook.com/documentation/instagram-platform/private-replies)
