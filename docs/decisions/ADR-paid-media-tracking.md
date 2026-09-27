# ADR — Tracking de compra para mídia paga

- **Status:** `PENDING_FOUNDER_DECISION`
- **Data:** 25/09/2026
- **Decisor:** fundador
- **Nada deste documento foi implementado na Meta.**

## Contexto

- A compra acontece no WhatsApp, fora do site. O site não tem checkout, então um Pixel de `Purchase` no site **não é possível** no funil atual.
- O worker de mídia paga só libera objetivo `PURCHASE` com dataset presente e `META_PURCHASE_EVENT_VERIFIED=true`. No funil atual ele fica em `BLOCKED` por construção (`PURCHASE_TRACKING_UNVERIFIED`).
- A landing gera `Referência da visita: VT-XXXX`, mas esse valor é o **hash das UTMs**, não de cada visita. Todo visitante sem UTM recebe o mesmo `VT-RJ5MO2`, então a conciliação só chega ao nível da campanha.
- A landing aceita apenas `utm_source`, `utm_medium`, `utm_campaign` e `utm_content`. O `fbclid` é descartado, logo não existe identificador de clique da Meta para reenviar.
- O Orders Ledger (`/os/orders`) existe desde esta rodada (IMPLEMENTED + TESTED, não DEPLOYED). Ele guarda `visit_ref` e UTMs sem dados pessoais.

## Fatos da documentação oficial da Meta (consultada em 25/09/2026)

- `action_source` aceita `chat` ("conversion was made via a messaging app, SMS, or online messaging feature"), `physical_store`, `business_messaging` ("from ads that click to Messenger, Instagram or WhatsApp"), `website` (exige `event_source_url`), entre outros.
- O `event_time` pode ter **até 7 dias** antes do envio.
- Eventos offline precisam estar associados a um **dataset**.
- Para anúncios de clique para o WhatsApp, a compra é atribuída com `action_source=business_messaging`, `messaging_channel=whatsapp` e o `ctwa_clid`. Esse identificador chega pelo webhook da **WhatsApp Business Platform**, não pelo app comum com link `wa.me`.

## Opções

### 1. Otimizar para clique ou conversa no WhatsApp e conciliar manualmente no Orders Ledger

- **Mede:** cliques no link e visitas à landing (e conversas iniciadas, se o anúncio for de clique para o WhatsApp). Mede também o evento de contato no botão do WhatsApp, se for instalado um Pixel com evento `Contact`, o que hoje não existe.
- **Não mede:** compra, receita e ROAS na plataforma. Isso sai do ledger, conciliado por campanha via UTM e `visit_ref`.
- **Esforço:** baixo. É preciso fazer deploy do ledger, padronizar UTMs nos anúncios e, opcionalmente, instalar o Pixel `Contact`.
- **Risco:**
  - a atribuição fica no nível da campanha;
  - a qualidade depende de registrar toda venda (menos de 30 s por venda);
  - otimizar por conversa pode trazer curiosos.
- **`META_PURCHASE_EVENT_VERIFIED`:** continua `false`. O worker precisa de um modo explícito de "conversa + conciliação manual"; sem isso, continua BLOCKED. É uma mudança de código que depende desta decisão.

### 2. Conversões offline ou de chat via Conversions API, alimentadas pelo Orders Ledger (só desenho)

- **Mede:** compras reais enviadas à Meta (`Purchase` com `action_source=chat`, ou `business_messaging` com `ctwa_clid`). Permite otimizar e atribuir por compra.
- **Não mede:** sem identificador (hash de telefone ou e-mail, `fbc` ou `ctwa_clid`), a Meta não consegue ligar a compra ao clique. Hoje o ledger proíbe dados pessoais e a landing descarta o `fbclid`.
- **Esforço:** médio.
  - Um job diário para respeitar a janela de 7 dias.
  - Token de sistema com escrita no dataset.
  - `event_id` para evitar duplicidade.
  - Validação no Test Events.
  - Decisão de base legal/consentimento (LGPD) para usar hash do telefone.
  - Ou adoção da WhatsApp Business Platform para receber o `ctwa_clid`.
- **Risco:** LGPD; escrita externa (exige aprovação no momento da ação); envio em dobro; lacuna de match que distorce a otimização.
- **`META_PURCHASE_EVENT_VERIFIED`:** só pode virar `true` depois que eventos `Purchase` reais aparecerem no dataset e forem conferidos no Events Manager (verificação LIVE).

### 3. Checkout no site (fora do escopo)

- **Mede:** `Purchase` pelo Pixel e pela Conversions API no site, que é o fluxo padrão da Meta.
- **Não mede:** vendas que continuarem no WhatsApp.
- **Esforço:** alto. Envolve gateway, estoque, frete e prazo com fonte para cada item, termos, LGPD e testes de ponta a ponta.
- **Risco:** construir checkout antes de validar economics e logística internacional.
- **`META_PURCHASE_EVENT_VERIFIED`:** pode virar `true` pelo caminho padrão, depois de validação LIVE.

## Recomendação (aguarda decisão)

**Opção 1 agora, com dois pré-requisitos, e a opção 2 reavaliada depois.**

1. Fazer deploy do Orders Ledger e registrar toda venda, inclusive as duas relatadas, com dados reais.
2. Decidir se a `visit_ref` passa a ser **única por visita** (por exemplo, `VT-<campanha>-<sufixo aleatório>`). Isso muda o texto da mensagem do WhatsApp e deixa a conciliação no nível do pedido.
3. Escolher o formato do piloto: anúncio de clique para o WhatsApp (mede conversas, mas pula a landing e a `visit_ref`) ou tráfego para a landing (mantém a `visit_ref` e as UTMs).

Reavaliar a opção 2 quando houver vendas reais registradas e uma decisão sobre consentimento e identificadores. Até lá, `META_PURCHASE_EVENT_VERIFIED=false`, `PAID_MEDIA_LIVE_WRITES=false` e `PAID_MEDIA_AUTONOMY_MODE=SHADOW`.

**Justificativa:** a opção 1 é a única que não exige coletar dado pessoal nem escrever em sistema externo, e pode ser medida com a infraestrutura que já existe. Ela troca precisão de atribuição por segurança e velocidade. Isso é adequado a um piloto de R$ 20/dia, que de qualquer forma não gera volume para validar CPA de compra (ver `docs/evidence/economics-2026-09-25.md`).
