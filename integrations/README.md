# Webhook de leads — Veratus

## Desenvolvimento local

```powershell
cd integrations
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:FRONTEND_ORIGINS = "http://localhost:8080"
python webhook.py
```

O endpoint é `POST /webhook`; a verificação é `GET /health`.

O serviço também entrega a pasta `landing` em `/`; abra `http://127.0.0.1:5000` para testar o fluxo completo. Em produção, a landing e o webhook ficam sob o mesmo domínio (por exemplo, `https://veratus.com/webhook`), mantendo `action="/webhook"`.

## Variáveis de ambiente

Copie `.env.example` como referência. Nunca inclua chaves de API em arquivos versionados.

- `MAILERLITE_API_KEY`: token da API atual do MailerLite.
- `MAILERLITE_GROUP_ID`: opcional; grupo que receberá os novos leads.
- `FRONTEND_ORIGINS`: origens permitidas, separadas por vírgula, quando landing e API estiverem em domínios diferentes.
- `LEADS_CSV_PATH`: backup local dos leads. Em produção, use disco persistente ou mantenha MailerLite como registro principal.

Crie no MailerLite os campos personalizados `whatsapp`, `source`, `utm_source`, `utm_medium`, `utm_campaign`, `utm_content` e `utm_term` antes de ativar o envio de mídia.
