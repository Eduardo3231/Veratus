# Google Sheets via Zapier (opcional)

O MailerLite é o registro principal dos leads. Use uma planilha apenas se houver uma finalidade operacional clara, pois ela também passa a conter dados pessoais.

1. Crie um Zap com **Webhooks by Zapier — Catch Hook** e depois **Google Sheets — Add Spreadsheet Row**.
2. Faça o Zap receber dados do servidor, nunca diretamente do navegador: assim a URL do Zapier não fica pública e o consentimento é validado antes do encaminhamento.
3. Se essa integração for implementada, envie somente `email`, `whatsapp`, `source`, UTMs e `timestamp`; configure acesso restrito à planilha.
4. Mapeie as colunas e faça um teste com um lead de teste.
5. Defina uma regra de deduplicação pelo e-mail e uma rotina de retenção/exclusão para dados antigos.

Antes de ativar, documente a finalidade da planilha na política de privacidade e limite o acesso às pessoas que realmente precisam operar os leads.
