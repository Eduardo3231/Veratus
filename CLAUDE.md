# Veratus: memória do projeto

@AGENTS.md

Leia o topo de `docs/HANDOFF.md` antes de editar: é o diário das rodadas e das decisões do fundador. Esta página guarda só o que vale para toda sessão.

## Regras que não mudam

- **Autonomia total (fundador, 04/11/2026).** O agente pode ligar chaves, criar cron e webhook, fazer merge na `main` por PR (o Render publica) e atuar nos canais sem pedir confirmação a cada ação. Em troca: ligue um canal por vez, comece em `SUPERVISED` ou `AUTO_SAFE`, confira na produção depois de cada passo, registre a ação, o horário e o resultado em `docs/HANDOFF.md`, e diga ao fundador o que não conseguiu fazer e o que ele precisa habilitar. Toda flag nova **nasce** `false` ou `SHADOW` no código.
- Segredo só por variável de ambiente, declarada em `.env.example` e `render.yaml` (`sync: false` para segredo). Nunca em código, teste, log, fixture ou doc.
- O repositório é público. Custo, margem, fornecedor e dado de cliente ficam no banco (Product Master e Orders Ledger). Teste usa custo fictício e monta CPF em tempo de execução.
- Fornecedor não aparece em site, marketplace, copy, metadata, log ou prompt.
- Para o cliente, só fatos `CONFIRMED` de `knowledge/fatos.yaml`. Joia: "tom dourado" ou "tom prateado". Proibido: ouro, prata, banho, folheado, semijoia, zircônia, hipoalergênico, urgência ou escassez falsa.
- Sem DM fria (`tests/test_no_cold_outreach.py`). Fora da janela de 24 h, só template aprovado; marketing só com opt-in.
- Pagamento só é confirmado pelo provedor (webhook validado e leitura de volta), nunca por print ou mensagem.
- Não usar mídia da quarentena, não apagar marca de foto, não cadastrar produto com marca de terceiros (Rolex, Omega e outras) sem nota fiscal e autorização.
- Papéis de agente novos podem ser criados e supervisionados, mas nunca recebem `PUBLISH`, `SPEND`, `SEND_MARKETING`, `WRITE_PRICE` nem `EXTERNAL_WRITE` (o código barra em tempo de execução). O limite financeiro e o guarda de escrita externa do código continuam valendo.
- Segredo exposto (em título, chat, log ou doc) é segredo comprometido: troque no provedor antes de seguir.

## Decisões do fundador

- O agente de vendas se chama **Harvey** e se apresenta como assistente virtual (nunca se passa por pessoa).
- **Facebook é o marketplace principal** (Messenger da Página, venda direta pelo link do Mercado Pago, catálogo da Meta).
- Canais do perfil (`branding/profile-kit.md`): Instagram e Stories levam ao WhatsApp; o WhatsApp fecha; o TikTok é descoberta.
- O fundador só entra quando o cliente pede uma pessoa ou ameaça ação jurídica.
- WhatsApp comercial: +55 11 95832-3612 (`harvey.WHATSAPP_NUMBER`, `scripts/build_landing.py`).
- **Prata 925 (09/11/2026):** o produto passa a ser colares e outros acessórios em prata 925 autêntica. Para o cliente, “prata 925” só depois de virar fato `CONFIRMED` em `knowledge/fatos.yaml` (nota do fornecedor e teste da peça), liberado no gate pelo `gate_issue` do fato; até lá o filtro continua barrando “prata”.
- Foco em correntes e joias de revenda (19/10, antes da prata 925): fotos do fornecedor com autorização de uso declarada pelo fundador; preço por multiplicador sobre o custo, definido pelo fundador em `JEWELRY_PRICE_RULE` (fora do Git). Importação: `scripts/importar_joias.py`; o fornecedor nunca aparece no que é público.
- **25 agentes (04/11/2026):** os 19 de antes mais a equipe de análise (supervisora, gestão, marketing, contabilidade, vendas) e o agente de pedidos dos marketplaces. Cada canal tem a análise das quatro áreas (`veratus_agents/channel_analysis.py`, `GET /os/canais/analise`); `POST /os/expediente` roda o dia sozinho.
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
