# Rodada de 27/09/2026 — site sem mídia de terceiros e referências dos posts

Branch `claude/site-referencias`, a partir de `claude/rodada-2609`. Local, sem push e sem deploy. Nenhum envio real; flags inalteradas.

## Decisão do fundador (FOUNDER_CONFIRMED 2026-09-27)

As peças físicas dos relógios **não** levam marca de terceiros. A marca está só nas fotos do fornecedor. Registro: `FOUNDER_DECISIONS["watch_physical_marks"]` em `veratus_agents/commercial_config.py`.

## O que foi encontrado

A verificação não parou no catálogo: toda a mídia de relógio do site trazia marca de terceiros. Com o Flask servindo a pasta `landing/` inteira, os 47 arquivos abaixo estavam publicamente acessíveis:

| Mídia | Arquivos | O que aparece |
| --- | --- | --- |
| Catálogo | 9 | ROLEX e a coroa no mostrador; Submariner, GMT-Master II, Datejust |
| Vídeo do hero (desktop e mobile) e poster | 3 | relógios no pulso a partir de 2 s; Submariner azul no poster |
| Campanha | 13 | caixas com relógios de marca |
| Produtos e peças interativas | 10 | selo verde "ROLEX S.A. GENEVE", coroa na parede |
| Posts e reel sociais | 12 | as mesmas peças; a fila social usava essas URLs |

## O que mudou (5 commits)

| Commit | Tema |
| --- | --- |
| `9d9611b` | **fix(site): retirar do ar a mídia com marca de terceiros.** `git mv` dos 47 arquivos para `quarantine/third-party-marks/` (manifesto e README; fora do Docker). Hero com a paisagem do início do vídeo e movimento lento em CSS. Relógios sem foto mostram o símbolo e "Foto oficial em produção" (`image_status: NEEDS_REAL_PHOTO`). O publicador social recusa mídia em quarentena antes de qualquer HTTP. Roteiros de campanha e post marcados como bloqueados. |
| `d03289b` | **feat(site): capítulo 3D do símbolo.** Referências @meshworkstudio e @designby.abhay: o V com espiga ganha espessura, gira com a rolagem e inclina com o ponteiro; os pontos Precisão, Equilíbrio e Permanência acompanham a face. |
| `38d1ba4` | **feat(site): "Como comprar".** Escolha → WhatsApp com referência → confirmação de disponibilidade, valor e pagamento → entrega em até 7 dias com frete grátis e sem taxas → desistência em 7 dias. |
| `d1280c0` | **feat(site): vitrine editorial das joias.** Halo Verde, Lumière e Celeste Anklet, com nome, alt e imagem do Product Master; só "tom dourado"/"tom prateado"; sem preço; "Tenho interesse" com produto e referência. |
| `aa2ed48` | **test(site): site → agente.** Resposta do Instagram com UTMs → landing → "Tenho interesse" → mensagem → canal do WhatsApp. A conversa nasce com produto, referência e UTMs. |

Todas as animações novas mexem só em `transform`. Sem JS, sem suporte a animação por rolagem ou com movimento reduzido, o conteúdo continua visível e parado (lição da correção de 25/09).

## Efeito no restante do sistema

- `discover_products()`: os 9 relógios agora têm `missing_fields: ["images"]`. O worker de mídia paga não encontra candidato, e isso está correto: não há anúncio sem foto real.
- Peso da página: o vídeo de 10,5 MB (desktop) e 1,4 MB (mobile) saiu. O hero agora usa uma imagem de 43 KB (desktop) ou 20 KB (mobile).
- A fila social (`social/publishing-payloads.json`) aponta para mídia em quarentena. `validate_payload` recusa esses itens.

## Evidência

- `docs/evidence/screens-2026-09-27/home-1440x900.jpg` e `home-390x844.jpg`: página inteira depois da mudança.
- `tests/test_third_party_media.py`, `tests/test_landing_resilience.py`, `tests/test_landing_browser.py` e `tests/test_founder_decisions.py` cobrem quarentena, referências inexistentes, ausência de vídeo, nenhuma requisição à mídia removida, cartões de relógio sem foto, 3D só com movimento, termos da jornada, vitrine espelhando o Product Master e o fluxo site → agente.

## Não verificado ao vivo

Deploy, Render e o comportamento em produção. O site público ainda roda `38f50be`, com a mídia de marca no ar, até o deploy aprovado.

## Segunda parte (27/09, tarde): relógios à venda e agente que responde

Pedido do fundador: colocar os produtos no ar, parar de travar a vitrine e melhorar os agentes. As fotos com o nome e a coroa de outra marca continuam fora do site; todo o resto foi aplicado.

| Commit | Tema |
| --- | --- |
| `3e92e54` | **Loja.** 9 relógios (Black GMT de volta) com R$ 289,90, "Frete grátis · até 7 dias" e botão "Pedir" no cartão e no diálogo. Sem foto real, cada relógio é desenhado na própria paleta (`palette` no Product Master) e identificado como "Ilustração da cor"; o ponteiro de segundos segue a hora de São Paulo e para com movimento reduzido. Hero com as condições confirmadas. |
| `f7855d5` | **Agente de vendas.** O gate bloqueava qualquer R$, frete e prazo, e o agente não conseguia responder preço. Agora os fatos confirmados passam com `evidence_ref`; variações, preço de relógio para joia, marcas de terceiros e "réplica" são bloqueados. Ferramentas sem custo, fornecedor e material. Instruções com passo a passo de venda e dois exemplos aprovados pelo próprio gate. |
| `31cd3c9` | **Fotos reais.** `incoming/relogios/<id>.jpg` → `python scripts/import_watch_photos.py --sem-marca-de-terceiros` → WebP 4:5 sem EXIF, `REAL_PHOTO` no Product Master, catálogo exportado. |

Verificação: `pytest -q` 221 passed (inclui os testes de navegador com Chrome: 9 cartões com preço, link "Pedir" com `produto=black-gmt` e intenção de pedido, diálogo com preço e ilustração), `ruff check` e `ruff format --check` sem apontamentos. Capturas atualizadas em `screens-2026-09-27/`.

Anúncio pago e post de relógio seguem bloqueados até a foto real do item exato.
