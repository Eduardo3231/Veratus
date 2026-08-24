# Veratus — base de pré-venda

Landing page, captação de leads, integração com MailerLite e materiais iniciais de marca e aquisição.

## Estrutura

- `landing/`: página de lista VIP e política de privacidade inicial.
- `integrations/`: webhook Flask pronto para container e integração com MailerLite.
- `automations/`: playbooks de prospecção, tráfego e nutrição.
- `branding/`: guia de marca e templates de publicação.

## Publicação

1. O serviço Flask já entrega a landing e o webhook no mesmo domínio, para que o formulário envie para `/webhook`.
2. Configure `MAILERLITE_API_KEY` e, se aplicável, `MAILERLITE_GROUP_ID` no provedor de hospedagem.
3. Crie no MailerLite os campos personalizados documentados em `integrations/README.md`.
4. Troque a política provisória pelo CNPJ/razão social, e-mail de privacidade e texto jurídico revisado antes de campanhas pagas.
5. Faça um cadastro de teste e confirme o lead no MailerLite antes de abrir tráfego.

O `render.yaml` é um ponto de partida para publicar o serviço completo no Render. Caso decida separar landing e API no futuro, defina `FRONTEND_ORIGINS`.
