# Chatwoot (caixa única) e Twenty (funil de leads)

Os dois nascem desligados (`CHATWOOT_SYNC_ENABLED=false`, `TWENTY_SYNC_ENABLED=false`). Ligados, nada muda para o cliente: o Harvey continua atendendo, e você ganha uma caixa única e um funil.

## Chatwoot: todas as conversas num lugar

O que faz (`veratus_agents/chatwoot.py`, rota `POST /integrations/chatwoot/webhook`):

- Cada conversa do WhatsApp, Instagram Direct, Messenger e perguntas do Mercado Livre aparece numa caixa do tipo API, com etiqueta do canal. Fica em "pendente" enquanto o Harvey atende.
- Quando o cliente pede uma pessoa ou reclama, a conversa abre, ganha a etiqueta `fundador` e uma nota privada com o motivo.
- O que você responde no Chatwoot sai pelo canal do cliente como mensagem sua (`human_written`), com a regra da janela de 24 h. A conversa passa a ser sua no Veratus.
- Ao marcar como resolvida, a conversa volta para o Harvey.
- Telefone, e-mail, CPF/CNPJ, CEP e endereço são apagados antes de ir ao Chatwoot. O endereço do pedido fica só cifrado no Veratus. O contato aparece como "Cliente · Canal".

Para ligar (só você):

1. Decidir onde o Chatwoot roda: Chatwoot Cloud (pago por agente) ou instância própria (custo de servidor). Em qualquer caso o Chatwoot vira operador de dados pessoais (LGPD): registrar no aviso de privacidade.
2. Criar uma caixa do tipo **API** e anotar o id da conta e da caixa.
3. Criar um token de acesso de um usuário administrador.
4. No Render: `CHATWOOT_BASE_URL` (https), `CHATWOOT_ACCOUNT_ID`, `CHATWOOT_INBOX_ID`, `CHATWOOT_API_TOKEN`, e `CHATWOOT_WEBHOOK_SECRET` (assinatura do Chatwoot) ou `CHATWOOT_WEBHOOK_TOKEN` (token na URL).
5. No Chatwoot, cadastrar o webhook `https://veratus.onrender.com/integrations/chatwoot/webhook` (com `?token=...` se usar o token) nos eventos `message_created` e `conversation_status_changed`.
6. Ligar `CHATWOOT_SYNC_ENABLED=true` com o canal ainda em SHADOW e conferir uma conversa de teste.

## Twenty: o funil de vendas

O que faz (`veratus_agents/crm_twenty.py`, rotas `GET /os/crm/twenty` e `POST /os/crm/twenty/sync`, só admin):

- Cada conversa vira uma pessoa e uma oportunidade. A etapa segue o funil: conversa nova (NEW), pediu preço ou produto (SCREENING), disse sim (MEETING), pedido ou link (PROPOSAL), pago (CUSTOMER). Nunca volta etapa.
- Valor pelo preço público do produto, em reais. Nada de custo ou margem.
- Sem nome, telefone ou e-mail do cliente: a pessoa é "Cliente · Canal". Levar o contato ao CRM é uma decisão sua (consentimento e LGPD).
- Desligado, `POST /os/crm/twenty/sync` devolve só a prévia do que seria feito.

Para ligar (só você):

1. Decidir entre Twenty Cloud ou instância própria.
2. Criar uma chave de API em Settings > APIs.
3. Conferir se as etapas do funil são as padrão (NEW, SCREENING, MEETING, PROPOSAL, CUSTOMER); se mudar, ajustar `FUNNEL_STAGES`.
4. No Render: `TWENTY_API_URL` (https) e `TWENTY_API_KEY`; depois `TWENTY_SYNC_ENABLED=true`.
5. A sincronia roda quando você chama `POST /os/crm/twenty/sync`. Um agendamento automático é um cron no Render, que só você cria.

## A conferir na hora de ligar

- Chatwoot: o formato da assinatura do webhook da versão em uso.
- Twenty: o formato das respostas da API REST, os nomes `amountMicros` e `pointOfContactId` e o limite de chamadas.
