# Veratus: memória do projeto

@AGENTS.md

Leia o topo de `docs/HANDOFF.md` antes de editar: é o diário das rodadas e das decisões do fundador. Esta página guarda só o que vale para toda sessão.

## Regras que não mudam

- Nada sai para fora. Toda flag nova nasce `false` ou `SHADOW`. Não ligue flag, não faça push na `main` nem deploy sem a confirmação do fundador no momento da ação. Push só na branch da sessão.
- Segredo só por variável de ambiente, declarada em `.env.example` e `render.yaml` (`sync: false` para segredo). Nunca em código, teste, log, fixture ou doc.
- O repositório é público. Custo, margem, fornecedor e dado de cliente ficam no banco (Product Master e Orders Ledger). Teste usa custo fictício e monta CPF em tempo de execução.
- Fornecedor não aparece em site, marketplace, copy, metadata, log ou prompt.
- Para o cliente, só fatos `CONFIRMED` de `knowledge/fatos.yaml`. Joia: "tom dourado" ou "tom prateado". Proibido: ouro, prata, banho, folheado, semijoia, zircônia, hipoalergênico, urgência ou escassez falsa.
- Sem DM fria (`tests/test_no_cold_outreach.py`). Fora da janela de 24 h, só template aprovado; marketing só com opt-in.
- Pagamento só é confirmado pelo provedor (webhook validado e leitura de volta), nunca por print ou mensagem.
- Não usar mídia da quarentena, não apagar marca de foto, não cadastrar produto com marca de terceiros (Rolex, Omega e outras) sem nota fiscal e autorização.
- Não criar cron ou webhook nos painéis da Meta, do Mercado Pago ou do Render. Não criar papel de agente sem a confirmação da seção 0.

## Decisões do fundador

- O agente de vendas se chama **Harvey** e se apresenta como assistente virtual (nunca se passa por pessoa).
- **Facebook é o marketplace principal** (Messenger da Página, venda direta pelo link do Mercado Pago, catálogo da Meta).
- Canais do perfil (`branding/profile-kit.md`): Instagram e Stories levam ao WhatsApp; o WhatsApp fecha; o TikTok é descoberta.
- O fundador só entra quando o cliente pede uma pessoa ou ameaça ação jurídica.
- WhatsApp comercial: +55 11 95832-3612 (`harvey.WHATSAPP_NUMBER`, `scripts/build_landing.py`).
- Foco em correntes e joias de revenda (19/10): fotos do fornecedor com autorização de uso declarada pelo fundador; preço por multiplicador sobre o custo, definido pelo fundador em `JEWELRY_PRICE_RULE` (fora do Git). Importação: `scripts/importar_joias.py`; o fornecedor nunca aparece no que é público.
- Poucos testes: só os que provam a regra. Economize token.

## Mapa do código

- `integrations/webhook.py`: Flask (webhooks da Meta, Mercado Pago, marketplaces; rotas `/os/*` com token de admin).
- `veratus_agents/conversations.py`: `ConversationEngine` (modos SHADOW, SUPERVISED, AUTO_SAFE, AUTO), classificador de intenções e gate `policy.hard_review_customer_reply`.
- `veratus_agents/channel_adapters.py`: WhatsApp, Instagram Direct, Messenger, Mercado Livre e cola manual.
- `veratus_agents/harvey.py`, `sales_flow.py`, `orders.py`, `payments.py`, `workflow.py` (`AGENT_REGISTRY`, `SALES_INSTRUCTIONS`).
- `knowledge/`: fatos, skills de venda (`knowledge/skills/*.md`, até 800 caracteres cada) e casos de conversa.
- `scripts/simular_operacao.py`: a operação inteira com o código real e clientes fictícios.
- `landing/`: o site (não publicado nesta fase).

## Antes de dizer "pronto"

- `ruff check . && ruff format --check .`
- `python -m pytest -q tests/<arquivo>` (o alvo); a suíte inteira uma vez antes do commit.
- `python -m veratus_agents.knowledge lint` e `python -m veratus_agents.knowledge eval` quando mexer em `knowledge/`.
- Mexeu em `landing/` ou `scripts/build_landing.py`: `python -m pytest -q tests/test_landing_browser.py` (o hook de sessão instala o `playwright==1.56.0`; sem ele os 14 testes pulam em silêncio).
- Skills locais: `systematic-debugging`, `verification-before-completion`, `requesting-code-review`, `webapp-testing`, `impeccable` (só texto).

## Economia de token

- O hook de sessão gera o grafo do código em `graphify-out/` (local, sem LLM). Antes de abrir arquivo grande: `graphify query "pergunta"`, `graphify explain Simbolo`, `graphify affected caminho.py`.
- No HANDOFF, leia a seção do topo; as antigas só quando o assunto voltar.
