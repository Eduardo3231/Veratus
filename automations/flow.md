# Fluxo de Automação Inicial — Veratus

Objetivo: capturar leads da landing e converter com comunicação rápida (email + WhatsApp) para gerar vendas de pré-venda.

1) Captura (landing `index.html`)
  - Campos: `email` (obrigatório), `whatsapp` (opcional)
  - Enviar para: webhook (Zapier/Integromat) ou endpoint simples que grava em planilha/CRM.

2) Tagging / Segmentação
  - Tag inicial: `lead_prep-venda`
  - Se veio por anúncio X: tag `ads_x`

3) Sequência de e-mail (exemplo 3 mensagens)
  - Email 1 (imediato): Confirmação + promessa de oferta em 48h. CTA: confirmar WhatsApp.
  - Email 2 (24h depois): Conteúdo sobre diferenciais (logo, trigo, design). CTA: link para página de produto.
  - Email 3 (48h — oferta): código exclusivo de lançamento com tempo limitado.

4) WhatsApp (via API ou ManyChat/360dialog)
  - Template inicial: mensagem curta + link para checkout pré-venda.
  - Evitar spam — ligar frequência a abertura de e-mail e cliques.

5) Conversão e pós-venda
  - Ao comprar: acionar e-mail de confirmação + número de rastreio quando disponível.
  - Pedir avaliação social 7 dias após entrega.

Integrações sugeridas:
- Zapier / Make (Integromat) para separar captura → CRM
- MailerLite / SendGrid / Brevo para envios de email
- 360dialog / Twilio / Z-API para WhatsApp business
- Google Sheets ou Airtable inicialmente como CRM leve
