# Economics por canal — 25/09/2026

> **Atualizado em 26/09/2026:** o fundador confirmou frete grátis e nenhuma taxa ao cliente. O EconomicsSnapshot vigente dos relógios está em `veratus_agents/commercial_config.py` (`WATCH_ECONOMICS_SNAPSHOT`) e em `docs/evidence/founder-round-2026-09-26.md`: INCOMPLETE em `shipping_cost_paid_by_veratus`, `payment_fee` e `tax`.

**Resultado: `INCOMPLETE` nos dois canais.** Não há CPA de equilíbrio válido para liberar mídia.

- **SKU analisado:** `arctic-white`, o escolhido pelo worker em SHADOW. Os 9 relógios têm o mesmo preço e custo em `commercial_config.py`, então este economics vale para qualquer um deles.
- **Motor:** `calculate_economics` (worker de mídia paga), com fator de segurança 0,70.
- **Evidência bruta:** `docs/evidence/shadow-shift-2026-09-25.json`.

## Por que R$ 224,90 não é CPA de equilíbrio

`R$ 289,90 − R$ 65,00 = R$ 224,90 (77,58%)` é **margem bruta de produto**. Ela ignora pagamento, comissão, frete, tributos, importação, devolução e embalagem. Cada real desses custos reduz o CPA de equilíbrio em R$ 1,00 e o CPA-alvo em R$ 0,70. Por isso, R$ 224,90 serve apenas como **teto teórico**.

`POST /os/pricing/calculate` também não resolve. Ele responde a outra pergunta ("que preço dá X% de margem sobre o custo?") e trata todo componente ausente como `0`. Chamado só com o custo de R$ 65,00, devolve preço recomendado de R$ 92,86 e `max_cpa_brl` de R$ 27,86: um número calculado com zeros inventados, que não se refere ao preço atual de R$ 289,90. **Não use esse endpoint para CPA de mídia até todos os componentes terem fonte.**

## EconomicsSnapshot — WhatsApp direto

| Componente | Valor | Estado | Fonte ou o que falta |
| --- | --- | --- | --- |
| Preço de venda | R$ 289,90 | CONFIRMED | configuração comercial confirmada pelo fundador |
| Custo do produto | R$ 65,00 | CONFIRMED | configuração comercial confirmada pelo fundador |
| Taxa de pagamento | `null` | UNVERIFIED | Meio padrão (PIX direto, link de pagamento, cartão) e taxa efetiva não registrados |
| Comissão do canal | `null` | UNVERIFIED | Venda direta, provavelmente sem comissão. Confirmar se há intermediário, como link de pagamento ou plataforma. |
| Frete (custo e quem paga) | `null` | UNVERIFIED | Origem internacional (PRIVATE), pacote de 350 g (18 × 14 × 10 cm). Faltam transportadora, custo e política. |
| Impostos sobre a venda | `null` | UNVERIFIED | Regime tributário da Veratus não informado |
| Tributos de importação | `null` | UNVERIFIED | Remessa internacional: falta saber quem recolhe (vendedor ou cliente na entrega) e em qual regime |
| Custo esperado de devolução ou recusa | `null` | UNVERIFIED | Sem histórico. Inclui frete de retorno internacional. |
| Embalagem | `null` | UNVERIFIED | **Não modelada** no motor atual |

## EconomicsSnapshot — Mercado Livre

| Componente | Valor | Estado | Fonte ou o que falta |
| --- | --- | --- | --- |
| Preço de venda | R$ 289,90 | CONFIRMED (se mantido no canal) | configuração comercial |
| Custo do produto | R$ 65,00 | CONFIRMED | configuração comercial |
| Tarifa de venda (comissão) | `null` | UNVERIFIED | Depende da categoria (o category discovery está bloqueado: `AUTH_REQUIRED`) e do tipo de anúncio |
| Taxa de pagamento | `null` | UNVERIFIED | Confirmar se a tarifa do canal já inclui o processamento |
| Frete | `null` | UNVERIFIED | A logística do ML para conta brasileira pressupõe origem no Brasil. **Não confirmado na documentação oficial**: a página retornou 403 para leitura automática. |
| Impostos sobre a venda | `null` | UNVERIFIED | Regime tributário |
| Tributos de importação | `null` | UNVERIFIED | Idem ao WhatsApp |
| Devolução | `null` | UNVERIFIED | Política do canal mais o retorno internacional |
| Embalagem | `null` | UNVERIFIED | Não modelada |

O motor atual **não tem dimensão de canal**: os dois canais recebem os mesmos campos e caem no mesmo `INCOMPLETE`. Os campos que faltam no motor são `payment_fees`, `channel_fees`, `shipping_subsidy`, `taxes` e `expected_returns_cost`. Embalagem e importação não têm campo próprio.

## Sensibilidade (cenários aritméticos, não estimativas)

`X` é a soma, por pedido, de todos os custos ainda desconhecidos. As linhas mostram só o efeito matemático de cada soma possível.

| X (R$) | Contribuição antes da mídia = CPA de equilíbrio | CPA-alvo (× 0,70) | ROAS de equilíbrio | ROAS-alvo |
| ---: | ---: | ---: | ---: | ---: |
| 0 (teto teórico) | 224,90 | 157,43 | 1,29 | 1,84 |
| 50 | 174,90 | 122,43 | 1,66 | 2,37 |
| 100 | 124,90 | 87,43 | 2,32 | 3,32 |
| 150 | 74,90 | 52,43 | 3,87 | 5,53 |
| 200 | 24,90 | 17,43 | 11,64 | 16,63 |

O teto de teste de R$ 140 comporta poucas vendas dentro do alvo: 0,9 venda no teto teórico (140 ÷ 157,43), 1,6 com X = 100 (140 ÷ 87,43) e 2,7 com X = 150 (140 ÷ 52,43). O piloto de R$ 20/dia pode medir **custo por conversa** no WhatsApp. **CPA de compra**, não mede com confiança.

## Risco logístico (não modelado)

A origem registrada é internacional (PRIVATE), com manuseio de 2 dias úteis. Para vender no Brasil isso implica:

1. **Prazo porta a porta desconhecido.** O site não promete prazo, o que está correto. Mas nenhum anúncio pode prometer prazo sem fonte.
2. **Tributação de importação.** Se a Veratus absorve, X sobe muito. Se o cliente paga na entrega, aumentam recusa e devolução (`expected_returns_cost`) e cai a conversão. Nenhuma das duas hipóteses está modelada.
3. **Regras de marketplace.** A elegibilidade de anúncio com origem internacional em conta brasileira do Mercado Livre não está confirmada. O programa cross-border (Global Selling/CBT) existe, mas funciona como conta internacional separada. Status: UNVERIFIED.
4. **Claims.** Frete, prazo e garantia só podem aparecer em anúncio ou atendimento com fonte para o item exato (AGENTS.md).

## O que o fundador precisa confirmar (e o impacto)

1. **Meio de pagamento padrão e taxa efetiva.** Sem isso, `payment_fees` fica `null` e o economics não fecha.
2. **Frete internacional por pedido:** transportadora, custo, prazo e quem paga. Provavelmente é o maior item de X.
3. **Tributos de importação:** quem recolhe e em qual regime. Define se X sobe ou se cai a conversão.
4. **Regime tributário da Veratus** e alíquota efetiva sobre a venda.
5. **Política e custo esperado de devolução ou recusa**, incluindo retorno internacional.
6. **Custo unitário da embalagem.** Também é preciso decidir se o motor ganha esse campo; hoje ele não existe.
7. **Mercado Livre:** elegibilidade da origem internacional, tipo de anúncio e tarifa da categoria (depende de autorizar o OAuth, ver `mercado-livre-readiness-2026-09-25.md`).
