# Rodada de 26/09/2026 — decisões do fundador e correções

Branch `claude/rodada-2609` (local, sem push, sem deploy). Flags inalteradas: `PUBLISH_ENABLED=false`, `PAID_MEDIA_LIVE_WRITES=false`, `WHATSAPP_SEND_ENABLED=false`, `INSTAGRAM_DM_ENABLED=false`. Nenhum envio real.

## Bloqueio: marca de terceiro nas imagens dos relógios

As imagens de catálogo dos relógios mostram marca de terceiro no mostrador. Recortes feitos em `landing/assets/catalog/*.webp`:

| SKU | O que aparece no mostrador |
| --- | --- |
| `black-gmt` | ROLEX, coroa, GMT-MASTER II |
| `arctic-white` | ROLEX, coroa, SUBMARINER |
| `ocean-blue` | ROLEX, coroa, SUBMARINER |
| `royal-blue` | sem marca legível; desenho de GMT com aro bicolor |
| `platinum-classic` | ROLEX, coroa, OYSTER PERPETUAL DATEJUST |
| `emerald-signature` | ROLEX, coroa, SUBMARINER |
| `silver-prestige` | ROLEX, coroa, OYSTER PERPETUAL DATEJUST |
| `polar-blue` | ROLEX, coroa, SUBMARINER |
| `bronze-heritage` | ROLEX, coroa, DATEJUST |

Com preço de R$ 289,90 e custo de R$ 65,00, essas peças não podem ser da marca exibida. Publicar preço e botão de pedido ao lado dessas imagens transforma a vitrine em oferta de produto com marca de terceiro. Isso contraria o `AGENTS.md` (sem alegação de marca de terceiros copiada) e as políticas da Meta e dos marketplaces sobre falsificação.

**Por isso não foram aplicados:** preço público dos relógios, botão de pedido por produto, retorno do Black GMT à vitrine e qualquer lançamento de mídia paga. As decisões estão registradas como FOUNDER_CONFIRMED em `veratus_agents/commercial_config.py`, e o bloqueio está em `STOREFRONT_BLOCKERS`.

**Para desbloquear:** fotos do item exato sem marca de terceiros e confirmação do fundador de que as peças físicas não levam marca de terceiros. Depois disso, exibir o preço é uma mudança pequena. Os 8 relógios que já estão no site público continuam com essas imagens hoje. Recomenda-se trocar ou retirar as imagens antes de qualquer divulgação.

As imagens das joias não mostram logotipo. O pingente de trevo preto (`noir-clover`) lembra um desenho conhecido de joalheria; vale revisar.

## Decisões do fundador (FOUNDER_CONFIRMED 2026-09-26)

| Decisão | Onde está | Situação |
| --- | --- | --- |
| Black GMT na vitrine | `FOUNDER_DECISIONS`, `STOREFRONT_BLOCKERS` | Ativo no Product Master, na API e nos agentes; vitrine **bloqueada** pela marca na imagem |
| Vendedor: São Paulo/SP, veratus.ltda@gmail.com, (11) 95832-3612 | `SELLER`; rodapé, privacidade, condições | Aplicado |
| CPF do vendedor | `VERATUS_SELLER_DOCUMENT` (`sync: false`), renderizado pelo Flask | Aplicado; vazio = linha omitida. Teste falha se um número no formato de CPF entrar em arquivo versionado |
| Fornecedor PRIVATE | `SHIP_FROM = {"visibility": "PRIVATE"}` | Nome e endereço saíram do código. O histórico do Git ainda contém esses dados |
| Entrega em até 7 dias; frete grátis; sem taxa extra | `DELIVERY_MAX_DAYS`, `FREE_SHIPPING`, `CUSTOMER_*_BRL`; condições de compra | Aplicado nas condições de compra |
| Preço de todos os produtos no site | não aplicado na vitrine | Relógios bloqueados pela marca na imagem; joias NEEDS_PRICING |
| Joias: material UNVERIFIED, só "tom dourado" / "tom prateado" | `catalog/products.json`; teste de copy | Aplicado nos 9 itens |

## Preços das joias

Nenhuma fonte real com preço foi encontrada. As 9 peças ficam `NEEDS_PRICING`, com `price_source: null`, e nenhum preço é exibido.

| SKU | Peça | Preço | Fonte |
| --- | --- | --- | --- |
| VRT-JWL-ANK-001 | Celeste Anklet | NEEDS_PRICING | — |
| VRT-JWL-NCK-001 | Halo Verde | NEEDS_PRICING | — |
| VRT-JWL-NCK-002 | Éclat Duo | NEEDS_PRICING | — |
| VRT-JWL-NCK-003 | Rosa Áurea | NEEDS_PRICING | — |
| VRT-JWL-BRC-001 | Lumière | NEEDS_PRICING | — |
| VRT-JWL-BRC-002 | Celeste Link | NEEDS_PRICING | — |
| VRT-JWL-ANK-002 | Essenza | NEEDS_PRICING | — |
| VRT-JWL-NCK-004 | Verde Aura | NEEDS_PRICING | — |
| VRT-JWL-NCK-005 | Noir Clover | NEEDS_PRICING | — |

Onde se procurou:

- `catalog/products.json`: `sale_price: null`, `financial_status: NEEDS_PRICING`.
- `landing/catalog.json` e `/api/catalog` público (lido em 26/09): nenhum campo de preço.
- `docs/` (fase 4 feminina, relatório de runtime, data discovery, evidências), `launch/` e `marketing/`: só aparecem preços de relógios (289,90 e o antigo 389,90) e do serviço de imóveis.
- `runtime/*.sqlite3`: rascunhos de marketplace só dos 9 relógios, a 289,90.
- `~/Downloads`: PDFs da Veratus (apresentação institucional, deck, contexto da equipe) sem preço de joia; nenhuma planilha de catálogo.
- Mercado Livre: não há credencial local; o conector do Claude exige autorização; o status em produção exige token de admin (não usado).

Para publicar um preço: preencher `sale_price` **e** `price_source` da peça no Product Master.

## EconomicsSnapshot dos relógios

| Campo | Valor | Estado |
| --- | --- | --- |
| price | 289,90 | CONFIRMED |
| unit_cost | 65,00 | CONFIRMED |
| customer_shipping | 0 | FOUNDER_CONFIRMED |
| customer_fees | 0 | FOUNDER_CONFIRMED |
| shipping_cost_paid_by_veratus | NULL | UNVERIFIED |
| payment_fee | NULL | UNVERIFIED |
| tax | NULL | UNVERIFIED |

Status `INCOMPLETE`, listando exatamente os 3 campos acima. `break_even_cpa` fica nulo e `break_even_cpa_final=false`.

**Não modelado (risco):** com frete grátis e direito de desistência de 7 dias, o custo de devolução e o frete de retorno recaem sobre a Veratus. O snapshot do fundador não tem campo para isso.

## Pergunta pendente ao fundador

O Product Master registra origem internacional (PRIVATE) e manuseio de 2 dias úteis. O fundador confirmou entrega em até 7 dias para todos os produtos. A entrega porta a porta em 7 dias é viável a partir da origem registrada? Também falta definir quando o prazo começa a contar (pedido ou pagamento).

## Estado mantido em memória de processo (WEB_CONCURRENCY=2)

| Estado | Onde | Risco com 2 workers | Tratamento |
| --- | --- | --- | --- |
| Comandos, tarefas, aprovações, auditoria, idempotência | `CommandEngine` (snapshot único) | **Perda e duplicação**: cada worker regravava o snapshot inteiro | Ler-mesclar-gravar sob trava do banco; idempotência sob trava; execução reservada (`claim`) |
| Experimentos, revisões, incidentes e idempotência da mídia paga | `PaidMediaStore` (snapshot único) | **Perda e duplicação** | Mesmo modelo; `plan()` e `approve()` sob trava |
| Aprovar/rejeitar em `/os/approvals` | memória do worker | **Perda**: a decisão não era gravada | Gravada sob trava |
| Leads | CSV em `/tmp` com trava de thread | **Perda** a cada deploy; escrita sem trava entre processos | Tabela `leads` no banco |
| Criação de tabelas | `CREATE TABLE IF NOT EXISTS` em 7 stores | 500 (`UniqueViolation`) no primeiro acesso de 2 workers a banco vazio | Trava consultiva `lock_schema()` |
| Dedupe de webhook WhatsApp/Instagram/Mercado Livre | tabelas com `ON CONFLICT` | nenhum | já no banco |
| OAuth state do Mercado Livre | tabela com consumo atômico | nenhum | já no banco |
| Token do Instagram | novo: tabela cifrada com reserva | nenhum | reserva no banco |
| Rate limit (`rate_store`) | memória | limite efetivo dobra (2 × 10/min) | mantido: não perde nem duplica dados |
| `resource_locks` (TaskEngine), `locks` (marketplace stores) | memória | nenhum: nunca usados | `resource_locks` removido; os outros dois ficaram (código morto) |
| Renovação do token do Mercado Livre | sem trava entre processos | baixo: uma leitura pode falhar, nenhum token é apagado | mantido |

Prova com 2 processos reais (`tests/test_multiworker_state.py`, SQLite e PostgreSQL 16 local via pgserver): antes da correção, 7 de 8 falhavam (registros apagados, chave com 2 comandos, chave com 2 experimentos, comando executado 2 vezes). Depois, 8 de 8 passam. Com 2 servidores Flask reais no mesmo PostgreSQL e requisições simultâneas, o mesmo comando e o mesmo experimento foram retornados pelos dois workers. Um comando criado no worker A aparece no B, e os 2 leads foram gravados. Houve 4 execuções limpas em banco vazio depois de `lock_schema()`; antes, 2 de 3 davam 500.

## Verificação

- `pytest`: 208 passed com `VERATUS_TEST_DATABASE_URL` apontando para o PostgreSQL local (inclui Playwright com Chrome). Sem essa variável, 204. No CI, os testes de navegador são pulados.
- `ruff check` e `ruff format --check`: ok. `compileall` e `git diff --check`: ok.
- Peso transferido na carga (servidor local): 390x844 caiu de 12,49 para 2,28 MB; 1440x900, de 12,49 para 11,45 MB.
- Capturas em `docs/evidence/screens-2026-09-26/` (1440x900 e 390x844): vitrine de relógios e de joias (sem preço, pelo bloqueio), modal, rodapé com contato, condições com frete e prazo.

## Não verificado ao vivo

Deploy, Render, rotas novas em produção, token do Instagram real, Meta e WhatsApp. O site público ainda roda `38f50be`: `/os/paid-media/status` e `/integrations/whatsapp/webhook` responderam 404 em 26/09.
