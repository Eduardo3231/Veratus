# HANDOFF — leia antes de editar, atualize antes de sair

**Última atualização:** 17/10/2026 (fim da noite), Claude Code: Facebook como canal principal (Messenger da Página e catálogo da Meta), correções da revisão independente (dados pessoais, chamadas ao fundador, marketplace, concorrência, pagamento) e painel ao vivo refeito. Nada foi ligado, publicado ou mandado para a `main`.
**Trava:** `python scripts/agent_lock.py status`. Só edite com a trava em seu nome (regra no `AGENTS.md`).

## Harvey no modelo da Veratus: canais do kit de perfil (17/10)

Pedido do fundador: "adapte ao nosso modelo para que possamos utilizá-lo, focando nos canais que instrui no perfil" (e "claro" ao nome Harvey). "Perfil" = `branding/profile-kit.md`, tabela "Papéis dos canais".

- **`veratus_agents/harvey.py`**: nome, apresentação, papéis dos canais do kit (`PROFILE_CHANNELS`), link para o WhatsApp e o relatório (`report`, só contagens).
- **Nome:** a primeira resposta de cada conversa em canal direto (WhatsApp, Instagram, Messenger) começa com "Olá! Aqui é o Harvey, assistente virtual da Veratus." Marketplace não leva (resposta pública no anúncio). Depois de o fundador devolver a conversa, ele se apresenta de novo. "Você é um robô?" responde "Sou o Harvey, assistente virtual da Veratus…".
- **Instagram e Stories → WhatsApp** (kit: "Site ou WhatsApp" e "WhatsApp"; estratégia: "enviar ao WhatsApp apenas quando a conversa pedir valores, disponibilidade ou atendimento"):
  - no Direct, valor, disponibilidade, pedido e "como compro" têm texto fixo que responde e manda `wa.me` com o produto e `utm_source=instagram`, `utm_medium=direct|stories` e `utm_campaign=harvey`;
  - o Direct não pede mais endereço, e um endereço colado lá é apagado da caixa;
  - o WhatsApp lê o link como lê o botão do site, e o Harvey continua no fechamento.
- **Stories:** a resposta a um Story (`reply_to.story` do webhook) fica marcada como origem `stories` (tabela nova `dm_origins`, criada sozinha) e vai no link.
- **Fluxo** (`sales_flow.py`, `GET /os/flow`): canais do perfil primeiro (Instagram Direct, Stories, comentários, WhatsApp, TikTok), cada um com função e próximo passo; depois os marketplaces, com o Facebook como principal. O TikTok entra como descoberta, sem conversa automática.
- **Aprender:** `GET /os/harvey` (admin) mostra conversas por canal, quantos o Instagram levou ao WhatsApp e quantos chegaram e compraram, e as objeções com quantas viraram pedido e pagamento.
- **Bug corrigido no caminho:** a linha "Origem da visita: utm_source=…" que o botão do site acrescenta em visita de campanha virava "a origem do envio". O cliente que pedia preço recebia "a equipe confirma" e o fundador ganhava uma tarefa à toa. O classificador agora ignora as linhas de atribuição.
- **Skill** `knowledge/skills/harvey-canais.md` e passo 9 nas instruções do modelo. O kit de perfil ganhou a seção "Harvey nos canais".
- **Simulação:** cliente 13 responde um Story, recebe o preço e o link, toca no link, fecha e paga no WhatsApp, e o pedido é despachado. Modo alvo: 13 leads, 7 pedidos, 6 pagos e enviados, 2 chamadas ao fundador. Hoje (SHADOW): o link fica no rascunho e ninguém chega ao WhatsApp.
- **Pendente:**
  1. O TikTok orgânico não tem integração: conteúdo e respostas a comentários seguem à mão, com as skills `ig-*`.
  2. A ligação do Instagram e do WhatsApp segue a ordem de "Para ligar cada canal".

## Método Harvey no agente de vendas (17/10)

- **Origem:** https://github.com/ethanplusai/harvey (commit `e114e95`, MIT; aviso em `knowledge/skills/HARVEY-LICENSE`). O Harvey é um agente de prospecção B2B (e-mail frio, LinkedIn, busca de contatos). Isso **não** foi usado: a Veratus não faz contato frio. Das 9 skills, valeram as de conversa: `sales_methodology`, `objection_handling` (LAARC), `offer_strategy` e as regras éticas.
- **Skills de venda** (`knowledge/skills/harvey-metodo.md`, `harvey-objecoes.md`, `harvey-fechamento.md`, `harvey-limites.md`, cada uma com menos de 800 caracteres, o corte do `LiveSalesModel`): vão no contexto do modelo de linguagem em toda conversa; `SALES_INSTRUCTIONS` ganhou o passo 8 (método Harvey).
- **Texto fixo no motor** (`conversations.py`, intenções de risco 0 em `POLICY_INTENTS`), antes tudo caía em "outro":
  - `objecao_preco` ("tá caro", "achei caro"): reconhece, mostra o que está incluído (frete grátis, sem taxas, desistência em 7 dias) e pergunta se pesa o valor ou o modelo; conta como pedido de desconto, então insistir vira `desconto_insistente`.
  - `hesitacao` / `hesitacao_final` ("vou pensar"): uma pergunta de diagnóstico; na segunda vez, porta aberta e silêncio.
  - `consultar_alguem` ("vou ver com minha esposa"): resumo do relógio com valor e prazo para mostrar.
  - `sem_interesse` ("não tenho interesse"): agradece e para, sem nova oferta.
  - `pergunta_ia` ("você é um robô?"): diz que é o assistente virtual da Veratus e oferece o responsável.
  - Versões de marketplace sem as condições da venda direta.
- **Fora, de propósito:** blefe, intimidação, urgência e "takeaway" encenado do personagem (prática abusiva no CDC e contra as regras daqui); e-mail frio, LinkedIn, multicanal e busca de contatos.
- **Verificação:** 5 casos novos obrigatórios em `knowledge/conversas-teste.yaml` (eval 18/18), `tests/test_harvey.py`, suíte inteira verde, simulação com as cenas do método (c2, c11, c12), mesmas 2 chamadas ao fundador.
- **Decisão do fundador:** dar ao atendente o nome "Harvey" para o cliente (hoje ele se apresenta como "assistente virtual da Veratus").

## Gestor do Instagram: skills `ig-*` (17/10)

- 13 skills de https://github.com/Jakeschincariol/instagram-agent-skill (commit `d03c56b`, MIT) em `.claude/skills/ig-*`; origem em `.claude/skills/instagram-agent-skill-ORIGEM.md`.
- Só rascunho: publicar continua pelo agendador social (`SOCIAL_PUBLISH_ENABLED=false`) e com confirmação do fundador.
- `ig-dm` ganhou no topo a regra "sem DM fria". A voz da Veratus para as skills fica em `social/instagram/voice.md`, que o hook de sessão liga em `~/.claude/instagram/voice.md`.

## Rodada de 17/10 (fim da noite): Facebook como canal principal e correções da revisão

Pedido do fundador: "Conclua o projeto e revise para concerto de qualquer erro na estrutura de agentes ou design do site. Quero que foque no Facebook como principal marketplace." Tudo na branch, com as flags novas desligadas e o canal em `SHADOW`.

- **Por que Messenger + catálogo:** a Meta não tem API pública para empresa anunciar no Facebook Marketplace no Brasil. O caminho oficial é a loja do Facebook (catálogo do Commerce Manager, feed CSV) trazendo o cliente e a conversa no Messenger da Página. É venda direta, como WhatsApp e Instagram: condições do site e link do Mercado Pago. Por isso `facebook` **não** está em `MARKETPLACES` (lá não se pode passar contato nem as condições do site).
- **Canal `facebook`** primeiro em `CHANNELS` e no `SALES_FLOW` (`sales_flow.py`): captação com `meta-agent`, `paid-acquisition-worker` e `social-media-worker`; negociação, fechamento e pagamento com `sales-agent`; pós-venda com `aftersales-agent`. Nenhum papel novo.
- **`FacebookMessengerAdapter`** (`channel_adapters.py`): mesma caixa cifrada do Direct (ids `fb_`), responde só a quem escreveu, janela de 24 h, `HUMAN_AGENT` só para texto de pessoa (até 7 dias), `messaging_type: RESPONSE`. Envia para `graph.facebook.com/<versão>/<META_PAGE_ID>/messages` com `META_PAGE_ACCESS_TOKEN`; com `FACEBOOK_MESSAGING_ENABLED=false` volta `BLOCKED`.
- **Webhook** `/integrations/facebook/webhook`: GET confere `FACEBOOK_VERIFY_TOKEN`; POST exige `X-Hub-Signature-256` com `FACEBOOK_APP_SECRET`, ignora eco da Página e dispara o processamento contínuo (se ligado).
- **"Olá, isso ainda está disponível?"** (a mensagem pronta do Facebook) virou a intenção `disponibilidade`: texto fixo sem prometer estoque ("a disponibilidade a equipe confirma junto com o seu pedido"). Fica depois de preço: o botão do site ("confirmar disponibilidade e valor") continua recebendo o preço.
- **Pedido pelo Messenger:** o ledger não aceitava o canal (`OrderChannel("facebook")` dava erro e a venda virava `ERRO_INTERNO`). Agora tem `OrderChannel.FACEBOOK` e `conversation_ref` com `fb_`.
- **Catálogo da Meta** (feito por um agente delegado e revisado): canal `facebook` em `listing_content.py` (título 200 e descrição 9999, suposição), `veratus_agents/meta_catalog.py`, `scripts/exportar_catalogo_meta.py`, `GET /feeds/meta-catalog.csv` (404 com `META_CATALOG_FEED_ENABLED=false`) e `GET /os/meta-catalog` (admin, só leitura). Entra no feed só peça com preço público, estoque **CONFIRMADO** na ficha, foto liberada de `landing/assets` fora da quarentena e texto aprovado no gate; nunca custo, margem, fornecedor ou contato. **Hoje 0 de 18:** sem estoque confirmado (17), relógios sem foto real e com mídia em quarentena (9), joias em `DRAFT` e sem preço (9), noir-clover em espera.
- **Variáveis novas** (`render.yaml`, `.env.example`, teste de paridade): `FACEBOOK_MESSAGING_ENABLED=false`, `META_CATALOG_FEED_ENABLED=false`, `CONVERSATION_MODE_FACEBOOK=SHADOW`, `FACEBOOK_APP_SECRET` e `FACEBOOK_VERIFY_TOKEN` (`sync: false`). A Página e o token reaproveitam `META_PAGE_ID` e `META_PAGE_ACCESS_TOKEN`.
- **Correções da revisão independente** (Legal Compliance Checker: 4 violações; Code Reviewer: 2 altas, 4 médias, 5 baixas):
  - **Dados pessoais:** alerta e tarefa levavam o texto cru do cliente (CPF, nome, endereço) para o WhatsApp do fundador e o painel. Agora o resumo é só o motivo ("O cliente pediu para falar com uma pessoa."); `summarize` também mascara CPF, CNPJ e CEP.
  - **Chamadas ao fundador:** pedido de pessoa só por frase de pedido e sem negação ("não quero falar com atendente", "presente pro meu gerente", "ligação com a marca" não chamam mais; vale também na entrada do WhatsApp). Reclame Aqui e "denunciar" viraram tarefa (insatisfação), não ação judicial. Ameaça jurídica é checada antes do pedido de pessoa e rebaixa o canal mesmo junto com ele. O disjuntor fica no painel, sem ping.
  - **Marketplace:** resposta sempre com texto fixo (com o modelo livre, prazo e preço do site saíam no Mercado Livre); o gate pega "7 dias", "289,90", "MercadoPago" e "link para pagamento" em qualquer forma. O desconto insistente no marketplace agora oferece de fato o responsável antes de marcar a oferta. O fato pendente não cita o assunto ("movimento", "resistência à água" reprovavam no próprio gate e disparavam o disjuntor).
  - **Joias:** o gate barra "dourado/prateado" sem "tom" quando a peça é joia.
  - **Anúncios:** cor do mostrador e tom dos relógios só da ficha confirmada (antes saíam da paleta do site, e o Platinum Classic dizia "Mostrador Branco" com a ficha dizendo "Prata claro"); a frase do site que cita cor ou tom sai do anúncio de relógio.
  - **Concorrência:** reserva por conversa (`BUSY`: a mensagem fica pendente e o processamento contínuo tenta de novo, até 3 vezes); desfecho gravado por mensagem; reserva sem desfecho há mais de 10 minutos (worker caiu no deploy) vira tarefa `ERRO_INTERNO` em vez de sumir; estado relido antes de enviar (o fundador assumiu durante o rascunho = vai para a fila).
  - **WhatsApp:** devolver a conversa ao BOT pela rota do WhatsApp agora devolve o motor junto (antes o canal voltava e o motor seguia mudo); a volta ao BOT esquece a oferta antiga; um "sim" depois de texto livre não vira catálogo. Runbook atualizado.
  - **Pagamento:** `payment_confirmed` também roda em `ALREADY_PROCESSED` (a volta anterior pode ter caído depois do PAID) e, se falhar, responde 500 para o Mercado Pago reenviar.
- **Simulação** (`scripts/simular_operacao.py`): 12 clientes fictícios, 3 pelo Facebook (loja do catálogo, anúncio que abre o Messenger, mensagem direta à Página). Modo alvo: 6 pedidos, 5 pagos e enviados (2 pelo Facebook), **2 chamadas ao fundador** (cliente pediu o dono; Procon), 5 itens no painel. Hoje: nenhuma escrita para fora. As chamadas agora são contadas pelos alertas reais (antes o ping do disjuntor ficava de fora). Painel no mesmo link, com o Facebook como raia e aba principais.
- **Verificação:** 572 passed e 4 skipped; `ruff check` e `ruff format --check` limpos; `knowledge lint` sem problemas e `knowledge eval` 13/13; `compileall`; sintaxe 3.11.
- **Decisões do fundador (cada uma na hora, com confirmação):**
  1. **Ligar o Facebook em SHADOW:** no app da Meta, assinar o webhook da Página (campo `messages`) em `/integrations/facebook/webhook` com o `FACEBOOK_VERIFY_TOKEN`; preencher `FACEBOOK_APP_SECRET`, `META_PAGE_ID` e `META_PAGE_ACCESS_TOKEN` (com `pages_messaging`) no Render. Depois de conferir os rascunhos, subir o nível e ligar `FACEBOOK_MESSAGING_ENABLED`.
  2. **Estoque:** confirmar `estoque` na ficha de cada relógio. Sem isso o feed fica vazio e a IA não diz "disponível".
  3. **Catálogo:** foto real dos relógios; preço e aprovação das joias; conferir no Commerce Manager os limites 200/9999 e se aceita WebP; só então ligar `META_CATALOG_FEED_ENABLED` e cadastrar a URL do feed.
  4. **Frete grátis no marketplace:** o gate não barra (pode valer pelas regras do Mercado Livre); as respostas fixas não prometem. Confirmar se fica assim.
  5. **`pedido_confirmado` fora da janela de 24 h:** o `WhatsAppAdapter` não tem `send_template` e os parâmetros do template aprovado não são conhecidos; o aviso vai para a fila.

## Rodada de 17/10: a IA vende e só chama o fundador quando o cliente pede

Pedido do fundador: "negociação com o cliente feita por IA (apenas me chamar, caso o cliente peça) -> conclusão da venda". Tudo na branch, com as flags como estavam (`SHADOW` e envios desligados). Feito com agentes da The Agency (lista no painel abaixo) e revisado por Code Reviewer e Legal Compliance Checker.

- **Política de passagem v2** (`veratus_agents/conversations.py`):
  - **CHAMADA** (estado `HUMAN` + alerta): o cliente pede uma pessoa (`PEDIDO_DE_ATENDENTE`, agora com "falar com o responsável", "me liga", "videochamada"), ou aceita a oferta do agente ("sim" curto logo depois da oferta); e a exceção de segurança, ameaça jurídica ou pública (`RECLAMACAO`: Procon, advogado, abrir processo, na justiça; Reclame Aqui virou tarefa na rodada seguinte). "Processo" sozinho não conta ("qual o processo de compra?").
  - **TAREFA** (painel, `delivery=PAINEL`, sem ping; a IA segue): conferência de pagamento (estorno, cobrado duas vezes), nota fiscal, atacado, personalização, fora de vendas, troca de endereço depois de pago, insatisfação sem ameaça, fato não confirmado e `ENVIAR_RASTREIO` (cliente pede rastreio de pedido já despachado; o ledger não guarda o código).
  - **Só a IA:** desconto em qualquer insistência (mantém o preço com fatos confirmados e oferece o responsável), "já paguei"/comprovante (a confirmação vem do Mercado Pago), "manda primeiro"/pago na entrega, Sedex e entrega amanhã, troca fora do prazo, IA travada (oferece o responsável em vez de passar).
  - `GET /os/conversations/metrics` ganhou `tasks_by_reason`; `handoffs_by_reason` conta só chamadas.
- **Conclusão da venda:** quando o webhook do Mercado Pago confirma (`payment_confirmed`), o cliente recebe "Pagamento confirmado pelo Mercado Pago..." pelo mesmo caminho de decisão (SHADOW enfileira; fora da janela de 24 h, só o template `pedido_confirmado`, que ainda não tem envio implementado: vai para a fila). Um aviso por pedido.
- **Link de pagamento pelo nível do canal:** `_order_and_link` agora consulta `operations.permission_allowed`; fora de `AUTO` o pedido é registrado, mas o link não é criado (`CHANNEL_MODE_<nível>`).
- **WhatsApp:** o pedido de pessoa na entrada (`wants_human`) usa a mesma lista do motor e gera exatamente uma chamada (`on_handoff` → `ConversationEngine.person_requested`).
- **Marketplace** (`policy.py` e texto fixo): o gate barra prazo em dias, valor em R$, Pix, Mercado Pago e link de pagamento nas respostas do Mercado Livre, Shopee, TikTok Shop e Amazon (frete grátis segue permitido). O texto fixo tem versão de marketplace para preço, frete, prazo, troca, pagamento, rastreio, fato pendente e pedido de contato ("por aqui mesmo eu te ajudo, e a compra é feita pelo próprio anúncio").
- **Fluxo contínuo** (`integrations/webhook.py`): `CONVERSATION_AUTOPROCESS_ENABLED` (padrão `false`, em `render.yaml` e `.env.example`). Ligada, cada webhook do WhatsApp e do Instagram que grava mensagem nova, e cada notificação `questions` do Mercado Livre, processa o canal em segundo plano (uma execução por canal por processo; o que chega no meio é pego na volta seguinte). Substitui o cron de `/os/conversations/process`.
- **Bugs corrigidos no caminho:**
  - o provedor de token do Mercado Livre chamava `from_env()` sem o store: o `TypeError` era engolido e **as perguntas do Mercado Livre nunca chegavam ao motor**;
  - falha do modelo de linguagem derrubava o laço do canal: agora cai no texto fixo (evento `MODEL_FAILED`), e uma mensagem com erro vira tarefa `ERRO_INTERNO` sem parar as outras.
- **Mapa do fluxo:** `veratus_agents/sales_flow.py` + `GET /os/flow` (admin): canal, origem do lead, papel por etapa (só ids do `AGENT_REGISTRY`), nível efetivo, chaves de envio (booleano) e métricas.
- **Estrutura de agentes** (`operations.py`, `marketplace_ops.py`): o contrato do `paid-acquisition-worker` era sobrescrito pelo laço genérico; o guard de publicação usava `marketplace-agent`, que não existe, e pulava a checagem de permissão; os 4 papéis de 13/10 agora nunca recebem PUBLISH, SPEND, SEND_MARKETING, WRITE_PRICE nem EXTERNAL_WRITE em execução; `START_HERE.md` apontava para cinco documentos inexistentes.
- **Docker:** `.dockerignore` excluía `social/` e o `Dockerfile` o copiava: **o build da imagem estava quebrado nesta branch** (reproduzido com `docker build`). A imagem leva só `social/calendario-*.json` e o `manifest.json` da quarentena, sem o qual o bloqueio de mídia com marca de terceiros ficava desligado dentro do serviço.
- **Anúncios** (`veratus_agents/listing_content.py`, `scripts/exportar_anuncios.py`): título, descrição e destaques por canal só com campos públicos e fatos confirmados, todo texto passando no gate do canal; bloqueios junto. Hoje: **0 de 18 prontos** em cada canal (joias sem preço e em `DRAFT`; relógios sem foto real, origem e ficha). Limites de título (60/120/200/255) são suposição a conferir no painel de cada canal.
- **Simulação** (`scripts/simular_operacao.py`): o serviço inteiro num diretório temporário, com Meta, Mercado Pago e Mercado Livre num roteador falso. Nove clientes fictícios, duas rodadas. Modo alvo: 4 pedidos, 3 pagos e enviados, 2 chamadas ao fundador (cliente pediu o dono; Procon), 3 tarefas. Hoje (padrões de produção): nenhuma escrita para fora, só a leitura das perguntas do Mercado Livre. Painel: https://claude.ai/artifact/52zdUdLvmqZTpafpKSU1fL (privado; o fundador compartilha).
- **Site** (`landing/`, `scripts/build_landing.py`): contraste do rodapé do hero abaixo do AA, 31 alvos de toque menores que 44 px no celular, anel de foco cortado nos cards (o `ul[role=list]` anulava a margem do trilho), foco fora da tela no carrossel e sob a barra fixa, páginas legais sem estilo desde 16/10, números de "Como comprar" saindo do counter CSS para o HTML. Sem mudança de texto, preço ou foto.
- **Skill img-to-html** (`design-systems/veratus-landing/`): etapa 1 (referência, wireframe tipado, plano) e etapas 2 a 5 (index.html sem CSS/JS inline, tokens no `:root`, uma classe por região). Diferença medida contra a referência: 0,01/255. É uma recriação de referência: `landing/` continua sendo o site.
- **Verificação:** suíte inteira verde (ver o último commit desta rodada), `ruff check` e `ruff format --check` limpos, `knowledge lint` e `knowledge eval` (13/13), `compileall`, sintaxe 3.11 conferida com `python3.11 -m py_compile`.
- **Pendências desta rodada:**
  1. Ligar os canais (ordem em "Para ligar cada canal", abaixo): cada flag é decisão do fundador na hora.
  2. Código de rastreio no ledger (hoje vira tarefa) e `send_template` do `pedido_confirmado` para fora da janela.
  3. `social/fila-publicacao.csv` e `social/publishing-payloads.json` apontam para mídia em quarentena; nada lê esses arquivos. Aposentar ou refazer quando houver mídia liberada.
  4. `integrations/agent_api.md` cita `docs/veratus-os.md`, que nunca existiu.
  5. O hook de sessão só vale para sessões novas depois de chegar à `main`; o merge na `main` faz deploy no Render.

## Sessões na nuvem: agentes da The Agency e dependências (17/10)

`.claude/hooks/session-start.sh`, registrado em `.claude/settings.json`, roda no início de cada sessão do Claude Code na nuvem (fora dela sai sem fazer nada):

- instala os 282 agentes de `msitarzewski/agency-agents` (MIT) fixados no commit `652b2692`, pelo instalador oficial, em `~/.claude/agents/`; um marcador evita reinstalar; sem rede para o GitHub a sessão abre sem eles;
- cria `~/.venvs/veratus` com `requirements-dev.txt` e põe esse Python no `PATH` da sessão.

Medido numa home limpa: 32 s na primeira vez, 0,7 s nas seguintes. Sessões novas clonam a branch padrão, então o hook só vale para elas depois que chegar à `main`. Trocar de versão da The Agency = trocar `AGENCY_COMMIT` depois de ler o diff deles.

## Site reestruturado em `landing/` (16/10): pronto na branch, sem deploy

O fundador aprovou a prévia ("pode aplicar tudo no site"). A reestruturação está em `landing/` na branch `ccr-c2f98235-0leyud`. **Não houve merge na main nem deploy**: a branch também leva todo o Veratus OS desde setembro, e publicar pede confirmação na hora.

- **Como a página nasce:**
  - `scripts/build_landing.py` monta `landing/index.html` a partir de `landing/catalog.json`.
  - `scripts/export_public_catalog.py` monta a página antes de gravar os dois arquivos; o importador de fotos reais passa por ele.
  - `test_page_is_built_from_the_public_catalog` falha se página e catálogo divergirem.
  - Todo produto, preço e link do WhatsApp aparece sem JavaScript.
  - `site.js` só acrescenta a referência da visita com UTM, a vista do relógio e os eventos do Pixel.
- **Separação:**
  - Relógios em três grupos de três: Clássicos, Esportivos e Marcantes. O Bronze Heritage, clássico e marcante, fica em Marcantes (`PRIMARY_STYLE`).
  - No celular, cada grupo é uma faixa que desliza.
  - Joias por tipo, em grade de quatro: colares, depois pulseiras e tornozeleiras lado a lado.
  - **Noir Clover fora do site** (`ON_HOLD`) enquanto o alerta de semelhança com marca de terceiros estiver aberto; ela segue no catálogo público e na API.
- **Igual ao ar:**
  - primeiro slide;
  - head de SEO e Open Graph, e o código do Pixel com os mesmos eventos;
  - mensagem e referência `VT-` do WhatsApp; identificação do vendedor, com o `<!--seller-document-->`.
- **Revisão:** a skill `code-review` apontou 10 itens; 6 foram corrigidos.
  - Exportação atômica.
  - "A partir de" quando os preços variarem.
  - Separador de milhar.
  - Acabamento de joia desconhecido não quebra a montagem.
  - Histórico ao Avançar e fechar a vista.
  - Gerador duplicado do protótipo removido.
  - Imagem provisória da vista do relógio: o GIF de 1×1 estava incompleto (imagem quebrada, invisível porque o script a troca antes de abrir); agora é um GIF transparente válido.
  - Ficaram como estão, de propósito: `ON_HOLD` no site (decisão pendente do fundador) e a fórmula independente do link no teste, que pega divergência do gerador.
- **Verificação:**
  - `pytest -q`: 423 passed, 3 skipped. Inclui os 14 testes de navegador, reescritos para o DOM novo e rodados com Playwright 1.56 no Chromium: computador e celular, com e sem animação, sem JS, JS atrasado, Pixel e atribuição site → agente.
  - `ruff check .` e `ruff format --check .` limpos.
  - Capturas 1440 e 390.
  - O CI não instala Playwright: os testes de navegador rodam só localmente ou com `VERATUS_LANDING_URL`.
- **Para publicar:** confirmação explícita do fundador para levar a branch à main (deploy no Render). Depois, conferir `/`, `?relogio=ocean-blue`, WhatsApp e Pixel no ar.

## Lote de fotos e vídeos de relógios (16/10): bloqueado

- **O lote:** o fundador mandou 52 arquivos `.crdownload` para ampliar o catálogo. São ZIPs de download interrompido; nenhum abre por inteiro. Os cabeçalhos dos primeiros 20 mostram pastas com nomes de marcas de terceiros (Rolex, TUDOR, Tag Heuer, OMEGA, Hublot, Breitling, U-BOAT, NIXON, Puma, Armani), além de "Super clone", "Rolex top" e "Pp".
- **Respostas do fundador:**
  - as pastas são "diferenciação de qualidade e preço";
  - autenticidade: "confirmaremos tudo depois";
  - origem: "revendedor autorizado";
  - público: homens de 23 a 45 anos.
- **Decisão:** nada desse lote entra no site, nos anúncios ou nos marketplaces. Não se edita foto para tirar marca.
  - Caminho (a), marca própria: relógios desenhados pela Veratus, sem marca de terceiros nem imitação de modelo conhecido.
  - Caminho (b), originais de marca: só com nota fiscal e autorização do fabricante, com a marca verdadeira na página.
  - O fundador ainda não escolheu entre (a) e (b).
- **Pendente:** confirmar por documento a origem dos nove relógios já no site, cujos criativos mostram marca de terceiros no mostrador (o mesmo risco que o conselho apontou).
- **17/10, novo pedido ("adicione esses modelos ao catálogo"):** 6 imagens. Duas são criativos da Veratus com mostrador "Yacht-Master / Superlative Chronometer" e selos sem comprovação ("Produto verificado", "Garantia de procedência", "Nota fiscal emitida"). Quatro são fotos de relógios com Rolex (Submariner, Yacht-Master, Datejust), Omega (Speedmaster, Seamaster) e Diesel no mostrador, incluindo modelos que não existem na linha oficial (Submariner de luneta branca, Rolex de caixa preta, Speedmaster Snoopy bicolor). **Não entraram**, pela mesma decisão acima; nenhum arquivo foi para o repositório. Seguem valendo os caminhos (a) e (b).

## Founder skill e o conselho (14/10)

- **Instalação.** O fundador pediu `/plugin install founder-skill`, mas o `/plugin` não existe na nuvem. As 11 skills de https://github.com/Jakeschincariol/founder-skill (commit `fd9385b`, MIT) foram copiadas sem alteração para `.claude/skills/`; a origem está em `founder-skill-ORIGEM.md`.
  - Os comandos ficam `/founder-board`, `/founder-competitors`, `/founder-consumer`, `/founder-pricing`, `/founder-offer`, `/founder-cfo`, `/founder-marketing`, `/founder-brand`, `/founder-ops`, `/founder-launch` e `/founder-plan`.
  - O `ruff.toml` exclui `.claude/` do lint para a cópia não mudar.
  - O `.gitignore` deixa de fora os arquivos do pack que levam custo, margem ou fornecedor (`founder/numbers.json`, `cfo*`, `ops.md`, plano, resumo, one-pager e `panel/`): o repositório é público.
- **Primeira reunião do conselho** (`/founder-board`):
  - Arquivos: `founder/idea.md`, `founder/board/` (resumo e três memos) e `founder/board.md`.
  - Voto: **3 INVESTE SE, nota média 3,7.**
  - Risco unânime: a vitrine mostra marca de terceiros que o relógio real não tem, além dos Alpes e do "aço" sem confirmação. Também unânimes: cliente indefinido e distribuição travada sem foto real.
  - Condições: foto real, ficha e garantia por escrito, cliente numa frase, teste de 14 dias com metas antes e joias em espera.
  - Para a lente Produto, o palco 3D da prévia também espera.
  - O conselho recomenda tirar os criativos do site, o que contraria a decisão do fundador de 27/09. A decisão é dele.
- **Pergunta aberta ao fundador:** quem é o cliente, numa frase, e o orçamento do teste. Sem isso, `/founder-consumer` e `/founder-cfo` não rodam bem.
- **Próximo passo do pack:** `/founder-competitors` (pesquisa pública) ou `/founder-consumer --quick` (20 compradores simulados).

## Reestruturação do site: a prévia de 14/10 (registro)

Aplicada em `landing/` em 16/10 (seção acima). `design/reestruturacao-2026-10/` fica como registro da prévia; o gerador dela saiu, e o site sai de `scripts/build_landing.py`.

- **Pedido do fundador:**
  - um modelo novo com as imagens originais dos relógios (`landing/assets/catalog/`);
  - tirar a página do símbolo 3D (`#inspecao`, "O símbolo ganha forma") e manter o primeiro slide;
  - 3D como combinado em 26/09: o relógio gira com a rolagem, na visualização do produto;
  - ver antes de confirmar.
- **Arquivos:**
  - `index.html` (congelado; era gerado pelo `gerar.py`, removido em 16/10);
  - `estilo.css` e `vitrine.js`;
  - `projeto.html` é o documento do projeto, com as capturas em `capturas/`;
  - `empacotar.py` gera um HTML único para ver no app.
  - Para ver localmente: `python3 -m http.server` na raiz e abrir `/design/reestruturacao-2026-10/`.
- **Estrutura:** primeiro slide (igual), Relógios (os 9 criativos inteiros + filtros), Joias (9 peças em colares/pulseiras/tornozeleiras), Como comprar, rodapé. Saem a abertura, a faixa, o manifesto como seção, a página do símbolo, a troca Relógios/Feminino e a barra de progresso.
- **Medido:**
  - Celular (390 px): 16,1 → 9,2 telas.
  - Computador (1440 px): 10,1 → 8,4 telas.
- **Palco 3D** (`?relogio=<id>` abre direto; o histórico do navegador fecha):
  - gira o **criativo inteiro** com a rolagem, sobre um mostrador dourado; o ponteiro inclina;
  - com movimento reduzido, a imagem fica parada;
  - não amplia o mostrador, porque os criativos têm marca de terceiros. A regra de 26/09 proíbe isso no 3D.
  - Com as fotos reais sem marca, o painel dá lugar à sequência de 60 quadros do brief.
- **Igual ao ar:** mensagem e referência `VT-…` do WhatsApp (com UTM), eventos do Pixel (ViewContent ao abrir, Contact no WhatsApp), preço, frete, prazo, desistência e identificação do vendedor. A prévia **não** carrega o código do Pixel: ele volta quando a página for para `landing/`.
- **Verificação:**
  - capturas 390/1440;
  - fluxo da vista: abrir, fechar, voltar, Esc, trocar de modelo, foco no título;
  - filtros e links com UTM;
  - movimento reduzido e página sem JS (as 18 peças visíveis, links preenchidos);
  - HTML único aberto em `file://` sem erros;
  - `pytest -q`: 408 passed, 4 skipped; `ruff check .` e `ruff format --check .` limpos.
- **Decisões abertas (fundador):**
  1. Aprovar a prévia ou pedir ajustes.
  2. Jost nos textos pequenos do slide (hoje Arial).
  3. Descrição do Black GMT cita "aço" com o material PENDENTE na ficha; sugestão: "Preto e uma presença visual definida por contrastes."
  4. Noir Clover (alerta de 26/09).
  5. Preço das joias.
  6. Alpes no slide (alerta de origem).
- **Depois do "ok":**
  1. Gerar com prefixos `assets/` e `""` e levar para `landing/`, com o Pixel de volta e `/api/catalog`.
  2. Atualizar `tests/test_landing_browser.py` e `tests/test_landing_resilience.py`, que citam a página do símbolo e a abertura.
  3. Capturas e conferência do WhatsApp/Pixel.
  4. Merge e deploy só com confirmação na hora.

## Rodada de 13/10 — canais, atendimento autônomo e redes sociais

O fundador aprovou a seção 0 do prompt nesta sessão, com os valores recomendados: papéis novos, atendimento em `SHADOW`, Pix + link do Mercado Pago com juros pagos pelo cliente e desconto máximo de R$ 0. A Fase 7 (iPhones) fica para depois do primeiro teste.

> **Pacote do fundador pendente.** Os arquivos do pacote (`config/channel_fees_2026-10.json` com fontes, `tests/golden/channel_economics_cases.json`, `knowledge/`, `social/calendario-2026-10.*` e as 33 artes em `landing/social/posts/2026-10/`) estavam só no VS Code local e não chegaram ao repositório. O código está pronto para recebê-los; os arquivos versionados hoje são provisórios e mínimos, marcados `PLACEHOLDER`/`UNVERIFIED`. **Lembrete ao fundador: entregar o pacote e as skills de venda (`knowledge/skills/*.md`).**

### Relógios sobem: ficha preenchida pelo fundador (13/10, à tarde)

O fundador decidiu que **os relógios sobem**: entram nos posts, nos anúncios e nos marketplaces, e ele preenche as informações que faltam. As travas que estavam no código viraram campos de uma ficha. Ao confirmar um campo, o relógio libera sozinho no que dependia dele, sem mudança de código.

- **Ficha:** `catalog/ficha-relogios.yaml`.
  - Cada campo tem `valor` e `status`. Para usar, preencha o valor e troque `PENDENTE` por `CONFIRMADO`.
  - Os valores que já estão lá (aço inoxidável, 41 mm, 350 g…) são **sugestões** e não são usados enquanto estiverem `PENDENTE`.
  - Nada de custo, fornecedor ou endereço de origem na ficha: o repositório é público.
  - **Depois de preencher, faça deploy (ou reinicie o serviço).** Origem, checklist e economia leem a ficha na hora; os fatos que o atendimento e o gate afirmam são carregados na inicialização.
- **O que falta:** `python -m veratus_agents.watch_sheet status`, que mostra o checklist por modelo e por canal (também em `GET /os/watches/readiness`). `python -m veratus_agents.watch_sheet check` só valida a ficha e aponta o campo com erro.
- **O que cada campo libera:**

| Canal | Espera |
| --- | --- |
| Site, WhatsApp, Direct | nada: o relógio já está à venda (9/9) |
| Post no Instagram/Facebook, anúncio pago | foto real (importador) |
| Mercado Livre, Shopee, TikTok Shop | foto real + `envio_do_brasil: true` + cidade, prazo de postagem, materiais, movimento, diâmetro, embalagem; por modelo: cores e estoque |
| Amazon | o mesmo + código de barras (`gtin`, ou `ISENTO`) |

- **Marketplace (verificado em 14/10, ainda não liga):** `prepare_listing` já sabe guardar o rascunho com `pending_info` (o que falta) e os campos confirmados em `payload.ficha`, e o `ExternalWriteGuard` barra a publicação enquanto houver pendência (`WATCH_INFO_PENDING:*`). Mas o rascunho é recusado antes: no comando de distribuição, o relógio sem `REAL_PHOTO` não fica pronto (`readiness: images`), e o `build_distribution_draft` exige status `APPROVED` e o QA do Product Master (fornecedor, disponibilidade, evidência de custo e de material). A ficha não alimenta esse QA. Ver a decisão 8 abaixo.
- **Origem:** `envio_do_brasil: true` + `CONFIRMADO` substitui a pergunta `delivery-vs-registered-origin`. Libera os marketplaces e tira o `BLOCKED_BY_ORIGIN` da economia por canal.
- **Atendimento e gate:**
  - material, movimento, resistência à água, medidas, garantia, envio e cores confirmados viram fatos (`relogios-*`) que o agente afirma, em vez de "a equipe confirma";
  - o gate libera exatamente o texto do fato, na frase inteira: "41 mm" passa se a ficha diz 41 mm, e "40 mm" continua bloqueado; "tem garantia vitalícia" continua bloqueado se a ficha só confirma 90 dias. "Mecanismo" só conta como movimento com o tipo (quartzo, automático…): "mecanismo de pagamento" passa;
  - marca de terceiros, origem, fornecedor, urgência, desconto e estoque nunca são liberados assim.
- **Calendário:** os posts de relógio entram `NEEDS_INFO` (esperando a foto real), não mais `BLOCKED`. Quando a foto chega:
  - o importador gera também o JPEG 1080 × 1350 em `landing/social/posts/watches/<id>.jpg`;
  - `refresh` (que também roda dentro de `run-due`) troca a arte-modelo pelo JPEG e manda o item para a aprovação semanal.
  - Item de relógio com arte final do fundador mantém a mídia dele.
  - Só o iPhone segue `BLOCKED` (Fase 7).
- **Não muda:** a foto com marca de terceiros (quarentena) não entra em post, anúncio nem marketplace, e o site segue com a ilustração/criativo de vitrine até a foto real chegar. Nada é publicado sem as flags e a aprovação.

### Fase 1 — Economia por canal

- `veratus_agents/channel_economics.py`: `load_fee_config` (schema `veratus.channel_fees/v1`, objetos imutáveis), `evaluate`, `price_for_target_margin`, `price_to_match` (preços terminados em ,90: varredura de real em real abaixo de R$ 250, busca binária acima) e `channel_price_plan`. Os tipos de produto são `jewelry`, `watch` e `smartphone`. O status é `COMPLETE`, `ESTIMATED` ou `INCOMPLETE`; um relógio em marketplace com origem não confirmada fica `BLOCKED_BY_ORIGIN`.
- `custo_maximo` = sobra − margem-alvo × preço. `cpa_maximo` = lucro − margem-alvo × preço. A margem-alvo padrão (20%) é `ASSUMPTION` no arquivo.
- `config/channel_fees_2026-10.json` é **provisório**: valores de memória pública, todos `UNVERIFIED`; só o frete direto do relógio (R$ 25) é `ASSUMPTION`. Nenhum canal chega a `COMPLETE` até o arquivo do fundador substituir este. Se o arquivo do pacote usar outro formato, adapte o loader (o formato esperado está na docstring do módulo).
- `paid_media.calculate_economics(..., channel=)`: taxa do canal, taxa de pagamento, frete, tributo, devoluções e embalagem vêm do motor. O worker usa `PAID_MEDIA_ECONOMICS_CHANNEL` (padrão `direct_card`, o mais caro). A autonomia continua exigindo `COMPLETE`.
- Rotas com `X-Veratus-Admin-Token`:
  - `GET /os/economics/<sku>?channel=`;
  - `POST /os/economics/simulate`;
  - `GET /os/pricing/channel-plan/<sku>`;
  - `POST /os/pricing/channel-plan/<sku>/request`, que abre um `PRICE_APPROVAL` e não aplica nada.
- Relógio em rascunho de marketplace: `prepare_listing` bloqueia com `NEEDS_REAL_PHOTO` e/ou `ORIGIN_UNCONFIRMED` (`commercial_config.watch_channel_blockers`).
- Testes: `tests/test_channel_economics.py`. Os casos dourados ficam *skipped* até o arquivo existir; as bordas (R$ 49,99/50, 78,99/79, 79,99/80) usam o arquivo provisório.
- **Custo real versionado (reportado, não apagado):** `OFFICIAL_PRODUCT_COST_BRL = 65.00` em `veratus_agents/commercial_config.py`, repetido em `marketplace_ops.py`, `data_discovery.py`, testes e `docs/evidence/founder-round-2026-09-26.md`. O repositório é público. Decisão do fundador: mover para o Product Master (banco) e trocar os testes por custo fictício.
- `scripts/agent_lock.py` ganhou bit executável no Git: o Ruff no Linux (CI) acusava EXE001.

### Fase 2 — Relógios prontos para o teste

- O gate (`policy.py`) bloqueia, para todo produto:
  - marca de terceiros, réplica, clone, primeira linha e "inspirado em";
  - movimento ou mecanismo, resistência à água, medidas (mm, cm, diâmetro) e garantia afirmada;
  - qualquer menção a origem, importação, alfândega ou "taxado";
  - claims de material de joia: ouro, prata, banhado, folheado, semijoia, zircônia, hipoalergênico;
  - urgência ou escassez ("só hoje", "por tempo limitado") e oferta de desconto ou cupom (`DISCOUNT_LIMIT_BRL=0`).
- `commercial_config.watch_economics_snapshot(channel)`: o EconomicsSnapshot dos relógios vem do motor. Frete direto R$ 25 = `ASSUMPTION`; pagamento e tributo `UNVERIFIED`, então o worker de mídia paga continua sem CPA final. `WATCH_ECONOMICS_SNAPSHOT` fica como registro histórico de 26/09.
- `watch_channel_blockers(produto, canal)`: lista o que falta para o canal (vazio = liberado), lendo a ficha `catalog/ficha-relogios.yaml`. Ver "Relógios sobem" acima, que substituiu o bloqueio por origem fixo no código.
- Fotos reais: o fluxo não mudou (`incoming/relogios/<id>.jpg` → `scripts/import_watch_photos.py --sem-marca-de-terceiros`). A quarentena não mudou.
- Testes: `tests/test_watch_readiness.py`.

### Fase 3 — Atendimento autônomo (Sales Agent V2)

- **Módulos:**
  - `veratus_agents/conversations.py`: motor, classificador, níveis, gate, fila, disjuntor, alertas, prontidão, métricas e eval;
  - `veratus_agents/channel_adapters.py`: adaptadores e alerta ao fundador;
  - `veratus_agents/payments.py`: Mercado Pago.
- **Pipeline:** classificar → contexto (`knowledge/`, produto, origem, pedido) → rascunho → revisor (no modelo real) → gate → decidir → adaptador → auditoria (`cv_events`). Se o gate reprova, há uma reescrita; se reprova de novo, a conversa vai para `HUMAN` com `IA_TRAVADA`. Mensagem repetida pelo webhook é processada uma vez só.
- **Níveis:** `CONVERSATION_MODE_<CANAL>` (padrão `SHADOW`).
  - `AUTO_SAFE` envia sozinho saudação, catálogo, produto, preço, frete, prazo e como pedir.
  - `AUTO` envia também fechamento, coleta de dados, link, status e rastreio.
  - Risco 2 e intenção desconhecida nunca saem sozinhos.
  - Mesmo em `AUTO`, nada sai sem a chave do canal (`WHATSAPP_SEND_ENABLED`, `INSTAGRAM_MESSAGING_ENABLED`, `MERCADO_LIVRE_ANSWERS_ENABLED`). Quando o envio é bloqueado, o rascunho volta para a fila com o motivo.
- **Gatilhos de passagem ao fundador** (`HUMAN`) — *substituídos pela política v2 de 17/10 (seção do topo)*: atendente, reclamação/Procon/Reclame Aqui, problema de pagamento (inclusive comprovante), fora da política (desconto pela 2ª vez, troca fora do prazo, entrega expressa), fraude (enviar antes de pagar, pagar fora do link, trocar endereço depois de pago), só uma pessoa faz (nota fiscal, ligação, personalização, atacado), fora de vendas, IA travada (3 mensagens sem avanço ou 2 reprovações do gate).
  - **Interpretação registrada:** o 9º gatilho, fato não confirmado, *não* põe a conversa em `HUMAN`. O agente responde "a equipe confirma" e o fundador recebe o alerta, mas a conversa continua (o prompt diz que ele "não bloqueia o resto da conversa"). Os outros oito param a conversa.
- **Alerta ao fundador:**
  - template de utilidade `alerta_fundador` para `FOUNDER_ALERT_WHATSAPP` (motivo, resumo de até 3 linhas sem telefone/e-mail, sugestão);
  - fallback por e-mail (`FOUNDER_ALERT_EMAIL` + `SMTP_*`);
  - sem nada configurado, o alerta fica em `GET /os/conversations/alerts`.
  - A conversa volta para `BOT` com `POST /os/conversations/<canal>/<id>/state` `{"state":"BOT","actor":...}`.
- **Disjuntor:** duas reprovações do gate em uma hora, ou uma reclamação, rebaixam o canal um nível (`cv_channel_caps`) e alertam o fundador. `GET /os/conversations/readiness` calcula os critérios (50 conversas com 90% aprovadas sem edição e nenhuma reprovação; uma semana com 95%; 10 vendas sem erro) e abre um `AUTONOMY_CHANGE`. Subir de nível é mudar a variável no Render, nunca automático.
- **Pagamento:**
  - preferência com `external_reference` = id do pedido, parcelado com juros do cliente;
  - `POST /integrations/mercado-pago/webhook` valida `x-signature` (HMAC, tolerância de 10 min) e lê o pagamento de volta antes de `PAID`: `approved`, mesmo pedido, valor ≥ pedido, BRL;
  - comprovante enviado pelo cliente vira passagem ao fundador, nunca confirmação.
- **Dados do cliente:** só depois do "sim" (fechamento), cifrados com `VERATUS_TOKEN_ENCRYPTION_KEY` em `cv_customer_data`. Nunca entram na auditoria, no log nem no Orders Ledger. **Proposta de retenção para o fundador decidir:** apagar 90 dias depois da entrega (`ConversationStore.purge_customer_data`); as obrigações fiscais ficam com o documento fiscal, não com a conversa.
- **Adaptadores:**
  - WhatsApp: atual, mais templates de utilidade e marketing só com opt-in registrado (`marketing_allowed`);
  - Instagram Direct: campo `messages` do webhook atual, `DirectInbox` cifrado, janela de 24 h, tag `HUMAN_AGENT` só para texto escrito por pessoa;
  - Mercado Livre: perguntas (`/my/received_questions/search`, `/answers`) e pós-venda (`/messages/packs`). O gate do canal proíbe link, @, telefone, e-mail e convite para outro canal;
  - Shopee, TikTok Shop e Amazon: `API_CONTRACT_UNVERIFIED`, o rascunho vai para a fila para o fundador colar.
- **Rotas** (admin):
  - `POST /os/conversations/process` `{channel}`: processa as mensagens novas. Precisa de um cron no Render a cada minuto, que é ação externa e **não foi criado**;
  - `GET /os/conversations`, `/queue`, `/metrics`, `/readiness`, `/alerts` e `/adapters`;
  - `POST /os/conversations/queue/<id>/approve|reject`;
  - `POST /os/conversations/<canal>/<id>/state`.
- **Métricas por canal e por nível:** tempo da primeira resposta, aprovadas com e sem edição, passagens por motivo, conversa → pedido, pedidos pagos e reprovações do gate.
- **`test_no_cold_outreach.py`:** responder no Direct exige endereçar pelo id de quem escreveu. O teste passou a permitir só `veratus_agents/channel_adapters.py` (como já fazia com `whatsapp.py`) e ganhou um teste novo provando que esse adaptador recusa quem nunca escreveu ou está fora da janela.
- **Testes:**
  - `tests/test_conversations.py`: do "oi" ao link em `AUTO` com cliente falso; cada gatilho; disjuntor; pagamento; contrato dos adaptadores;
  - pipeline pelas rotas com `requests.Session.post` e `requests.post` falhando em qualquer chamada: nada sai com as chaves desligadas.

### Fase 4 — Repertório (`knowledge/`)

- `veratus_agents/knowledge.py` carrega e valida `voz.md`, `roteiro.md`, `objecoes.md`, `fatos.yaml`, `ofertas.yaml` e `skills/*.md`.
- `CONFIRMED_SALES_FACTS` agora vem de `fatos.yaml` (só `CONFIRMED`), com os mesmos `evidence_ref`; os fatos embutidos ficam como reserva.
- Fatos novos confirmados:
  - `relogios-modelos`;
  - `relogios-sem-marca-de-terceiros`;
  - `formas-pagamento`: Pix ou link do Mercado Pago, juros do parcelado com o cliente (seção 0).
- Pendentes, que viram "a equipe confirma":
  - material, especificações, origem/prazo, preço das joias e garantia;
  - número de parcelas, que segue em `SALES_MUST_CONFIRM_WITH_TEAM`.
- `ofertas.yaml`: só `ACTIVE` dentro da validade chega ao agente. O exemplo é `DRAFT`, um kit sem desconto.
- `python -m veratus_agents.knowledge lint`: reprova pressão, escassez, material, garantia, origem, marca de terceiros, desconto e fornecedor (o gate ganhou "menção a fornecedor"). Linhas com "nunca", "não diga", "proibido" ou "evite" são regras e não são reprovadas.
- `python -m veratus_agents.knowledge eval`: roda `conversas-teste.yaml` com o modelo simulado (`TemplateSalesModel`); `--live` usa o modelo real e tem custo. Ambos rodam no CI (`verify.yml`) e o CI quebra em caso obrigatório.
- **Provisório:** `voz.md`, `roteiro.md`, `objecoes.md` e `conversas-teste.yaml` foram escritos nesta sessão a partir do prompt atual do agente. `skills/` está vazio. **Lembrete ao fundador: entregar as skills e o roteiro de venda do pacote.**
- `PyYAML` entrou em `integrations/requirements.txt`. O Dockerfile copia `config/` e `knowledge/`.

### Fase 5 — Agendador de redes sociais

- `veratus_agents/social_scheduler.py`:
  - tabelas `scheduled_posts`, `social_approvals`, `social_publications`, `social_media_keywords`, `social_insights` e `social_events`;
  - status `DRAFT` → `PENDING_APPROVAL` → `APPROVED` → `PUBLISHING` → `PUBLISHED` | `FAILED` | `NEEDS_MANUAL` | `NEEDS_VIDEO` | `BLOCKED`.
- **Importação:** `python -m veratus_agents.social_scheduler import social/calendario-2026-10.json`.
  - Aceita lista ou `{"itens": [...]}`, com nomes em português ou inglês (`data_hora`, `formato`, `midias`, `legenda`, `palavras_chave`, `produtos`, `status`).
  - Horário sem fuso = Brasília. Mídia sem URL vira `https://veratus.onrender.com/social/posts/2026-10/<arquivo>`.
  - Vídeo entra `NEEDS_VIDEO`; item só de TikTok entra `NEEDS_MANUAL`.
  - Relógio sem `REAL_PHOTO`, linha de iPhone e mídia em quarentena entram `BLOCKED`, mesmo que o calendário esqueça.
  - O calendário real não está no repositório (pacote pendente). O teste com um calendário sintético de mesma composição dá 44 itens: 31 para aprovação, 7 vídeos e 6 bloqueados.
- **Aprovação semanal:** `request-approval` abre um `PUBLISH_APPROVAL` por semana com os `content_hash`. O fundador aprova em `/os/approvals/<id>/approve`. Editar o item depois disso (`edit_item`) tira a aprovação.
- **Publicação:** `run-due`, por cron do Render a cada 5 min (ação externa, **não criado**). Com `SOCIAL_PUBLISH_ENABLED=false`, só devolve a prévia, sem chamar a Meta e sem item bloqueado. Ligado:
  - confere se cada mídia responde 200 `image/jpeg` e não está na quarentena;
  - publica por `meta_content_publisher` (estendido com `create_social_container` para IMAGE/CAROUSEL/STORY, `publish_facebook_page` e `read_facebook_permalink`) e lê o permalink de volta;
  - é idempotente por id + `content_hash`;
  - faz 3 tentativas (espera de 10, 20 e 40 min) e depois `FAILED` + alerta.
  - Story na Página do Facebook fica `NEEDS_MANUAL`. TikTok: `TikTokDraftAdapter` `UNVERIFIED`, devolve `NEEDS_MANUAL`.
- **Palavras-chave por post:** `instagram_comment_rules.json` ganhou `keyword_replies` (QUERO, CATÁLOGO, PRESENTE, TREVO, CORAÇÃO, LUZ, IPHONE). O id da mídia vem do registro de publicação, e a regra vale só no post que pediu a palavra (`CommentReplyQueue.enqueue(..., media_rules=)`). O envio continua dependendo de `INSTAGRAM_DM_ENABLED`.
- **Relógios (Fase 2.6):** os posts de relógio esperam a foto real em `NEEDS_INFO`. Com a foto, `refresh` troca a arte-modelo pelo JPEG da foto real e o item vai para `PENDING_APPROVAL` (ver "Relógios sobem"). Uma arte com o layout das joias pode substituir o JPEG por `edit_item(media=...)` quando chegar no pacote.
- **Métricas:** `insights` lê alcance, salvamentos, comentários e compartilhamentos em +24 h e +7 dias. `report --week` gera o relatório semanal, e `insights_csv` exporta no formato de `social/medicao.csv`.
- **Rota:** `GET /os/social/posts`.
- Variáveis: `SOCIAL_PUBLISH_ENABLED=false` e `META_PAGE_ID` (vazio). O Dockerfile copia `social/`.

### Fase 6 — Papéis novos (aprovados na seção 0)

| id | nível | pai | permissões |
| --- | --- | --- | --- |
| `customer-supervisor` | 2 | `general-manager` | READ_PRODUCT, READ_ORDERS, CONVERSE, HANDOFF, REPORT |
| `sales-agent` (Sales Agent V2) | 3 | `customer-supervisor` | READ_PRODUCT, CONVERSE, CREATE_ORDER_DRAFT, CREATE_PAYMENT_LINK, REPORT |
| `aftersales-agent` | 3 | `customer-supervisor` | READ_ORDERS, CONVERSE, REPORT |
| `social-media-worker` | 2 | `general-manager` | READ_PRODUCT, CREATE_DRAFT, SCHEDULE_APPROVED_CONTENT, READ_INSIGHTS, REPORT |

- O registro tem 19 papéis, e `/os/agents` lista os quatro novos. Nenhum tem `external_write` nem permissão de publicar, gastar ou enviar marketing.
- `operations.permission_allowed`:
  - `CONVERSE` só envia em `AUTO_SAFE` (risco 0) ou `AUTO` (risco ≤ 1);
  - `CREATE_PAYMENT_LINK` só em `AUTO`;
  - `SCHEDULE_APPROVED_CONTENT` só para item `APPROVED`.
- A auditoria operacional do gerente ("analise o estado atual…") passou a acionar os quatro: níveis por canal, links de pagamento, pós-venda e agendador. O comando de distribuição não os aciona.

### Verificação desta rodada (13/10)

- `pytest -q`: 408 passed, 4 skipped (depois da revisão do gate, em 14/10). Os pulados são os casos dourados e o calendário real, que esperam o pacote, além do teste de navegador já existente. Rodado em Python 3.13 e 3.11 (o do CI).
- Revisão do gate (14/10): os três casos foram reproduzidos antes da correção ("tem garantia vitalícia" e "água a 100 metros" passavam depois de confirmados; "mecanismo de pagamento" era bloqueado) e agora têm teste que falharia sem a correção.
- `ruff check .` e `ruff format --check .`: limpos.
- `python -m veratus_agents.knowledge lint`: 0 problemas. `python -m veratus_agents.knowledge eval`: 10/10 com o modelo simulado.
- Com todas as chaves desligadas, nenhum POST externo (teste com `requests` falhando em qualquer POST, pelas rotas). `run-due` desligado devolve só a prévia, sem item bloqueado.
- `requirements-dev.txt` ganhou `Pillow` (o teste do importador de fotos já dependia dele e o CI não instalava). `veratus_agents/__init__.py` importa o workflow só quando usado, para `python -m veratus_agents.<módulo>` rodar sem aviso.
- **Nada foi ligado nem publicado:** nenhuma flag, push para `main`, deploy, cron, webhook nem template criado na Meta, no Mercado Pago ou no Render.

### Para ligar cada canal quando as chaves chegarem

Sempre na ordem: chave → teste em `SHADOW` → readiness → aprovação → subir um nível. Cada mudança de variável no Render é decisão do fundador no momento da ação.

1. **Pacote:** commitar `config/channel_fees_2026-10.json` (substitui o provisório), `tests/golden/channel_economics_cases.json`, `knowledge/` (voz, roteiro, objeções, skills), `social/calendario-2026-10.json`/`.csv` e `landing/social/posts/2026-10/*.jpg`. Depois:
   - rodar `pytest -q tests/test_channel_economics.py` (os 60 casos);
   - rodar `knowledge lint` e `knowledge eval`.
2. **Processamento:** `CONVERSATION_AUTOPROCESS_ENABLED=true` (17/10) processa cada canal quando o webhook grava mensagem nova, sem cron. A alternativa continua valendo: cron no Render a cada minuto chamando `POST /os/conversations/process` com `{"channel":"whatsapp"}` (e um por canal ligado), com `X-Veratus-Admin-Token`.
3. **WhatsApp:**
   - aprovar na Meta os templates de utilidade `pedido_confirmado`, `pedido_enviado` e `alerta_fundador` (parâmetros: motivo, resumo e sugestão);
   - cadastrar `FOUNDER_ALERT_WHATSAPP` no Render;
   - manter `CONVERSATION_MODE_WHATSAPP=SHADOW` e acompanhar `/os/conversations/queue` e `/readiness`;
   - quando o readiness abrir o `AUTONOMY_CHANGE` e o fundador aprovar: `WHATSAPP_SEND_ENABLED=true` e o novo nível.
4. **Mercado Pago:**
   - `MERCADO_PAGO_ACCESS_TOKEN` e `MERCADO_PAGO_WEBHOOK_SECRET` no Render;
   - webhook no painel do Mercado Pago para `https://veratus.onrender.com/integrations/mercado-pago/webhook` (tópico Pagamentos);
   - testar com usuário de teste;
   - só então `PAYMENT_LINKS_ENABLED=true`.
5. **Instagram Direct:**
   - assinar o campo `messages` no app da Meta (o webhook é o mesmo de `/integrations/instagram/webhook`);
   - permissão `instagram_business_manage_messages`;
   - `CONVERSATION_MODE_INSTAGRAM` em `SHADOW`;
   - depois do readiness, `INSTAGRAM_MESSAGING_ENABLED=true`.
6. **Mercado Livre:** conta já conectada por OAuth (`scripts/conectar_marketplace.py mercado-livre`); `MERCADO_LIVRE_SELLER_ID` para pós-venda; depois do readiness, `MERCADO_LIVRE_ANSWERS_ENABLED=true`.
7. **Shopee, TikTok Shop e Amazon:** os rascunhos ficam na fila (`API_CONTRACT_UNVERIFIED`) para colar no painel. Para automatizar, falta verificar o contrato da API de chat de cada um.
8. **Redes sociais:**
   - `import`, depois `request-approval`, depois aprovar cada semana em `/os/approvals`;
   - cron no Render a cada 5 min com `python -m veratus_agents.social_scheduler run-due`;
   - `META_PAGE_ID` para publicar na Página;
   - conferir a prévia e então `SOCIAL_PUBLISH_ENABLED=true`;
   - `insights` diário por cron e `report --week` às segundas.
9. **Alerta por e-mail (opcional):** `FOUNDER_ALERT_EMAIL` e `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`.

### Decisões abertas desta rodada

1. **Pacote e skills de venda:** entregar os arquivos listados acima. Sem eles, taxas e repertório são provisórios.
2. Prazo de retenção dos dados do cliente (proposta: 90 dias depois da entrega).
3. Custo real de R$ 65 versionado (ver Fase 1): migrar para o Product Master e trocar os testes por custo fictício.
4. Gatilho "fato não confirmado": confirmar a interpretação (alerta sem parar a conversa).
5. Nível inicial de cada canal fora do WhatsApp: todos em `SHADOW`.
6. **Ficha dos relógios:** preencher `catalog/ficha-relogios.yaml` e importar as fotos reais (o fundador vai preencher). `python -m veratus_agents.watch_sheet status` mostra o que falta.
7. **Fase 7 (iPhones) não foi feita:** pelo prompt, só depois do primeiro teste de 14 dias. Itens de iPhone no calendário e posts ficam `BLOCKED` (`LINHA_IPHONE_DEPOIS_DO_TESTE`).
8. **Rascunho de marketplace do relógio antes da foto e do QA (decisão do fundador).** Opções:
   - (a) manter: o rascunho sai só quando o Product Master estiver completo e com foto;
   - (b) o QA aceita material e disponibilidade vindos da ficha, e o rascunho sai com a lista do que falta. Recomendo (b): a foto continua obrigatória para publicar;
   - (c) relaxar também a foto no rascunho.
9. **`estoque` por modelo na ficha.** O produto é sob encomenda (`INVENTORY_MODE = ON_DEMAND`) e tem teto por canal (`CHANNEL_STOCK_CAP_FALLBACK = 20`). Proposta: trocar o campo por "teto de estoque por canal". Como o repositório é público, quantidade real deve ficar no Product Master (banco), não na ficha.
10. **Limpeza pendente (não feita, fora do pedido):** `FOUNDER_OPEN_QUESTIONS`, `STOREFRONT_BLOCKERS` e `WATCH_ECONOMICS_SNAPSHOT` em `commercial_config.py` ainda descrevem a regra antiga do relógio. Os testes os citam, então ficaram. `veratus_agents/watch_sheet.py` tem 547 linhas e pode ser reduzido pela metade (o relatório de economia e o texto de status são o que mais pesa).

## Conexões de marketplace (28/09)

O fundador vende ou vai vender em Mercado Livre, Shopee e Amazon. Os três conectam em **modo leitura**; a publicação continua bloqueada pelas flags.

Comando único: `.\.venv\Scripts\python.exe scripts\conectar_marketplace.py <mercado-livre|shopee|amazon>`.
- Pede `VERATUS_ADMIN_TOKEN` sem ecoar.
- Espera o Render acordar.
- Diz o que falta configurar.
- Roda a inspeção do agente.

| Canal | O fundador faz | O sistema faz |
| --- | --- | --- |
| Mercado Livre | App ID e Secret Key no Render (`MERCADO_LIVRE_CLIENT_ID`, `MERCADO_LIVRE_CLIENT_SECRET`). Redirect URI no app: `https://veratus.onrender.com/integrations/mercado-livre/oauth/callback` | OAuth já existente. Após autorizar, o gerente roda a verificação: conta, categoria, atributos obrigatórios e busca por SKU |
| Shopee | App no Shopee Open Platform. Cadastra `SHOPEE_PARTNER_ID` e `SHOPEE_PARTNER_KEY` no Render. Domínio de redirect do app: `veratus.onrender.com` | `/integrations/shopee/oauth/start` → autorização da loja → `/oauth/callback/<state>` troca o código. Token de 4 h renovado sozinho, cifrado no canal `shopee`. Inspeção: dados da loja e categoria de relógio |
| Amazon | App privado no Seller Central → Autorizar. Cadastra `AMAZON_SP_LWA_CLIENT_ID`, `AMAZON_SP_LWA_CLIENT_SECRET` e `AMAZON_SP_REFRESH_TOKEN` no Render | Token LWA (1 h, em cache), endpoint NA, marketplace `A2Q3Y263D00KWC`. Inspeção: participação no Brasil e product type de relógio |

- **Configuração no Render:**
  - `VERATUS_TOKEN_ENCRYPTION_KEY` precisa existir (chave Fernet). O comando explica como gerar se faltar.
  - `SHOPEE_API_HOST`, `SHOPEE_REDIRECT_URI` e `AMAZON_SP_ENDPOINT`/`AMAZON_SP_MARKETPLACE_ID` são opcionais; o código tem os padrões.
  - As variáveis da Amazon estão declaradas no `render.yaml` e no `.env.example`. As chaves ficam com `sync: false`, e `AMAZON_ENABLED`/`AMAZON_PUBLISH_ENABLED` com `false`.
- **Não verificado ao vivo:** nenhuma das três contas foi conectada ainda. O host da Shopee para contas brasileiras (`partner.shopeemobile.com`) segue a documentação pública; se a Shopee indicar outro host, basta mudar `SHOPEE_API_HOST`.
- **Amazon Agent (15º agente, 28/09):** canal `amazon` registrado (desligado), `amazon-agent` sob o `distribution-supervisor`, rascunho de anúncio com product type e GTIN a confirmar, canal `amazon` no Orders Ledger. O gerente inclui a Amazon na auditoria de conexões e nos comandos ("prepare ... Amazon").
- **Criação de anúncio** segue ausente nos quatro canais de venda. Os relógios não devem ir a anúncio sem foto real (`STOREFRONT_BLOCKERS`).
- **Pasta principal:** o Codex deixou alterações não commitadas em `integrations/webhook.py`, `render.yaml`, `.env.example`, `veratus_agents/paid_media.py`, `veratus_agents/meta_webhooks.py`, `tests/test_paid_media.py` e na ADR de tracking. Commitar ou descartar essas alterações é decisão do fundador. Só depois a pasta principal pode puxar a branch `claude/marketplaces`, que também altera `webhook.py`.
- **Palco dos modelos** (branch `claude/experiencia-modelos`): pronto e testado, **não publicado** por decisão do fundador em 28/09.

## Estado do repositório

- **Publicado em 27/09 com autorização do fundador:** `git push origin main` até `4f9cb2f`, incluindo a restauração das imagens dos nove relógios.
- Produção (`https://veratus.onrender.com`) conferida depois do deploy:
  - `/health` 200;
  - `/catalog.json` com `price_brl` nos 9 relógios;
  - `/catalog.json` aponta os nove relógios para imagens distintas em `assets/catalog/`;
  - os nove arquivos WebP respondem 200; o MIME explícito `image/webp` foi acrescentado ao servidor na rodada final;
  - `VERATUS_LANDING_URL=https://veratus.onrender.com pytest tests/test_landing_browser.py`: 12 passed, 1 skipped.
- Pasta principal: `main`; `pytest -q` 221 passed e teste de produção da landing 12 passed, 1 skipped.
- Flags: `PUBLISH_ENABLED=false`, `PAID_MEDIA_LIVE_WRITES=false`, `PAID_MEDIA_AUTONOMY_MODE=SHADOW`, `WHATSAPP_SEND_ENABLED=false`, `INSTAGRAM_DM_ENABLED=false`, e as 8 flags de canal `false`.

## Arquivos de outro processo na pasta principal

Com autorização do fundador (27/09), foram guardados no stash "externo 25-26/09 … autorizado pelo fundador" (`git stash list`; recuperar com `git stash show -p` ou `git stash pop`):

- as versões antigas de `veratus_agents/catalog.py` e `veratus_agents/data_discovery.py`. Elas eram de 26/09, às 20:39 e 20:55, removiam a coleção feminina e a projeção pública e faziam `/os/readiness` dar 500;
- cópias não versionadas de docs de 25 e 26/09.

Continuam na pasta, sem commit e sem alteração desta sessão:

| Arquivo | Situação |
| --- | --- |
| `veratus_agents/product_master.py` | agente externo em 25/09: exige material também para joias (decisão abaixo); os 221 testes passam com ele |
| `integrations/zapier_to_sheets.md` | versão antiga que manda colocar a URL do Zapier no HTML público. Não usar |
| `social/fila-publicacao.csv` | formato alterado, origem desconhecida |
| `docs/data-discovery-2026-09-19.json` | contém nome e endereço do fornecedor. **Não versionar;** apagar ou guardar fora do repositório |

O Codex (PID 5968) estava em execução desde 10:34 de 27/09, sem escrever na pasta principal durante a rodada.

## O que mudou em 27/09 (tarde)

| Commit | O que muda |
| --- | --- |
| `3e92e54` | **Relógios à venda.** 9 relógios, Black GMT incluído, com R$ 289,90, "Frete grátis · até 7 dias" e o botão "Pedir" (WhatsApp com produto, referência e intenção de pedido). Sem foto real, cada relógio aparece como **ilustração da própria paleta** (`palette` no Product Master), identificada como "Ilustração da cor". O ponteiro de segundos segue a hora de São Paulo. Hero com R$ 289,90, frete grátis e até 7 dias. |
| `f7855d5` | **Agente de vendas.** Antes, o gate bloqueava qualquer R$, frete grátis e prazo, então o agente não respondia "quanto custa?". Agora `CONFIRMED_SALES_FACTS` libera exatamente preço dos relógios, sem taxas, frete grátis, entrega em até 7 dias e desistência em 7 dias, cada um com `evidence_ref`. Continuam bloqueados: outro valor ou prazo, preço de relógio para joia, marca de terceiros, "réplica", estoque, parcelamento e garantia. As ferramentas não entregam mais custo, fornecedor nem material ao agente. |
| `31cd3c9` | **Fotos reais em um comando**, descrito abaixo. |
| `4f9cb2f` | **Imagens dos nove relógios restauradas.** Cada criativo voltou ao modelo correspondente; o Product Master distingue o criativo de vitrine da foto real exigida para canais externos. |

## Meta Pixel (28/09)

O Pixel `1633870688525258`, pedido pelo fundador, está em `index.html`, `condicoes-de-compra.html` e `privacy.html`.

- **Eventos:**
  - `PageView` em toda página;
  - `ViewContent` ao abrir um produto;
  - `Contact` em qualquer clique num link do WhatsApp, com `content_ids`, `content_name` e, nos relógios, `value: 289.9` e `currency: BRL`.
- O texto da mensagem do WhatsApp nunca vai para a Meta.
- **Não carrega em `localhost`/`127.*`.** Os testes de navegador também bloqueiam `connect.facebook.net` e `www.facebook.com/tr`, mesmo contra produção, para visitas de teste não entrarem nas métricas.
- `privacy.html` ganhou a seção "Cookies e medição de anúncios".
- **Publicado em 28/09** (`fe4de26`, push autorizado pelo fundador).
- **Verificado em produção:**
  - o HTML traz o código;
  - `fbevents.js` e a configuração do Pixel carregam;
  - `fbq.getState()` mostra o Pixel `1633870688525258` com `eventCount: 1` (PageView).
- `ViewContent` e `Contact` estão cobertos pelo teste de navegador (`test_meta_pixel_tracks_product_views_and_whatsapp_contacts`).
- **Não verificado ao vivo:**
  - a chegada dos eventos no Gerenciador de Eventos (Testar eventos). O navegador automatizado não mostrou a requisição `/tr`;
  - a variável `META_PIXEL_ID` no Render, que o worker de mídia paga lê.
- Banner de consentimento de cookies: não existe. Decisão do fundador.
- A ADR de tracking continua pendente; o Pixel cobre a parte "Contact" da opção 1.

## Fotos reais dos relógios

**Publicado e verificado em 27/09:** por ordem direta do fundador, os nove criativos de catálogo voltaram à vitrine e foram religados aos modelos Arctic White, Ocean Blue, Black GMT, Royal Blue, Platinum Classic, Emerald Signature, Silver Prestige, Polar Blue e Bronze Heritage. Eles ficam em `landing/assets/catalog/`, todos com 1122×1402. O Product Master os classifica como `CATALOG_CREATIVE` e mantém `image_status: NEEDS_REAL_PHOTO`; assim, aparecem no site sem liberar anúncios, posts ou listings como se fossem fotos reais da peça.

1. Salve uma foto por modelo em `incoming/relogios/<id>.jpg`, por exemplo `ocean-blue.jpg` ou `black-gmt.jpg`. A pasta fica fora do Git.
2. Rode `python scripts/import_watch_photos.py --sem-marca-de-terceiros`.
3. O script recorta em 4:5, salva WebP sem EXIF e marca `REAL_PHOTO` no Product Master. O cartão troca a ilustração pela foto.

Anúncio pago e post de relógio continuam bloqueados até a foto real do item exato (`STOREFRONT_BLOCKERS`). As demais mídias antigas seguem em `quarantine/third-party-marks/`: não restaurar nem editar para apagar marcas.

## Verificar

```powershell
.\.venv\Scripts\python.exe -m pytest -q          # 226 passed em 28/09 (pasta principal)
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

- Os testes de navegador (`tests/test_landing_browser.py`) usam o Chrome local e são pulados no CI.
- `tests/test_multiworker_state.py` sobe 2 processos reais.

## Decisões abertas do fundador

1. Fotos reais dos 9 relógios para anúncio, post e listings externos. Os criativos antigos voltaram somente à vitrine por decisão direta do fundador; continuam bloqueados nos canais automáticos.
2. Origem registrada com manuseio de 2 dias úteis vs entrega em até 7 dias. Quando o prazo começa a contar?
3. `product_master.py` (material obrigatório para joias no QA): manter ou reverter.
4. Economics: frete pago pela Veratus, taxa de pagamento e tributo (UNVERIFIED).
5. Preços das joias: nenhuma fonte real (9 × NEEDS_PRICING).
6. Fornecedor e mídia com marca no histórico do Git: reescrever ou não.
7. `VERATUS_SELLER_DOCUMENT`, `INSTAGRAM_ACCESS_TOKEN` e `OPENAI_API_KEY` no Render; número do WhatsApp Cloud API. Formas de pagamento decididas em 13/10: Pix + link do Mercado Pago, juros do parcelado com o cliente.

## Não faça

- Não ligue nenhuma flag de envio ou publicação nem faça push ou deploy sem confirmação explícita no momento da ação.
- Não use a mídia de `quarantine/third-party-marks/` fora das nove exceções de vitrine registradas em `manifest.json`; não edite fotos para apagar marcas.
- Não "restaure" arquivos que você não alterou; registre aqui.
- Não versione `docs/data-discovery-2026-09-19.json` nem nada com nome, endereço ou cidade do fornecedor.

## Próximo passo sugerido

1. Preencher a ficha dos relógios (`catalog/ficha-relogios.yaml`) e importar as fotos reais; conferir com `python -m veratus_agents.watch_sheet status`.
2. Entregar o pacote do prompt de canais e as skills de venda (`knowledge/skills/`) e rodar os casos dourados.
3. Ligar o cron de `/os/conversations/process` com o WhatsApp em `SHADOW` e acompanhar o readiness.
