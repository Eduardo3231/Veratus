# Auditoria e estratégia social — Veratus

Data da análise: 14 de setembro de 2026.

## Escopo realmente verificado

Foram verificados o site público, a identidade existente, os quatro criativos editoriais enviados, treze imagens otimizadas da campanha, seis imagens do acervo original e o vídeo comercial de dez segundos.

O Instagram `@veratus_oficial` não aparece indexado nos mecanismos públicos consultados e bloqueou a leitura sem uma sessão autenticada. Por isso, números de seguidores, alcance, publicações atuais, bio ativa, destaques e configurações internas não foram inventados nesta auditoria. A configuração abaixo está pronta para ser aplicada assim que a conta estiver conectada.

## Referências usadas

- O guia da [Watch Dealer Websites](https://www.watchdealerwebsites.com/blog/facebook-instagram-guide-watch-dealers) trata a bio, os destaques e as três primeiras peças da grade como sinais rápidos de clareza e confiança.
- A [H&Co](https://www.itshco.com/blog/social-media-luxury-watch-jewelry-retailers-2026) recomenda usar vídeos verticais em Instagram e TikTok e transformar o conteúdo orgânico em fonte de aprendizado para as campanhas.
- A análise da [Fratello](https://www.fratellowatches.com/watch-brands-on-instagram-how-do-they-approach-it-and-how-do-they-differ/) mostra a importância de uma voz humana e conhecedora para evitar um perfil excessivamente impessoal.
- A seleção da [Hodinkee](https://www.hodinkee.com/articles/the-best-watch-instagram-accounts-to-follow-right-now) destaca closes, detalhes e bastidores como formatos recorrentes em contas interessantes do setor.
- A [Meta](https://www.facebook.com/business/ads/facebook-instagram-reels-ads) orienta o uso de vídeo vertical 9:16, áudio e elementos principais dentro da área segura para Reels.

## Diagnóstico dos criativos

| Área | Avaliação | Implicação |
|---|---|---|
| Identidade | Muito forte | Preto, dourado, caixa clara e emblema formam um sistema reconhecível. |
| Fotografia | Forte | O produto está centralizado e tem boa leitura no celular. |
| Variedade | Média | Muitas peças seguem a mesma composição; em sequência, o perfil pode parecer repetitivo. |
| Conteúdo humano | Baixo no acervo atual | Faltam pulso, rotina, bastidores reais e atendimento em cena. |
| Educação | Baixo no acervo atual | Faltam posts que ajudem a pessoa a escolher pelo estilo e pelo uso. |
| Conversão | Boa base | O site e o WhatsApp já formam um caminho direto, mas precisam de medição por origem. |

## Ajuste editorial

A grade deve alternar cinco funções:

1. **Desejo:** fotografia forte do relógio.
2. **Escolha:** comparação de cores e estilos.
3. **Identidade:** manifesto e assinatura da Veratus.
4. **Confiança:** processo, atendimento e respostas objetivas.
5. **Conversa:** perguntas simples que convidam a comentar ou chamar no WhatsApp.

Distribuição inicial:

| Pilar | Participação |
|---|---:|
| Produto e detalhe | 35% |
| Estilo e escolha | 25% |
| Vídeo e atmosfera | 20% |
| Atendimento e processo | 10% |
| Prova real, quando existir | 10% |

## Grade inicial 3 × 3

A fila foi montada na ordem real de publicação. Depois do nono post, o topo do perfil ficará assim:

| Linha | Esquerda | Centro | Direita |
|---|---|---|---|
| 1 | Silver Jubilee | Reel da coleção | Royal Blue |
| 2 | Emerald | Escolha por estilo | Ocean Blue |
| 3 | Silver Onyx | Visão da coleção | Navy Gold |

Essa composição mantém simetria sem colocar quatro cartazes quase iguais lado a lado. A ordem detalhada está em `social/fila-publicacao.csv` e as legendas em `social/legendas-prontas.md`.

## Regras visuais

- Feed: preservar o produto no centro e conferir o recorte da miniatura antes de publicar.
- Reel: 9:16, marca e textos dentro da área segura; usar a capa preparada.
- Stories: uma pergunta ou ação por quadro, fonte grande e contraste alto.
- Carrossel: primeira página com uma promessa clara; páginas seguintes com uma ideia por tela.
- Não aplicar filtros que alterem de forma relevante a cor do mostrador.
- Não publicar preço, estoque, prazo, material, origem ou condição comercial sem confirmação para o item daquele dia.

## Ritmo de 30 dias

- Segunda: peça principal do feed.
- Terça: Stories com enquete sobre estilo.
- Quarta: Reel ou comparação.
- Quinta: Stories de pergunta e resposta.
- Sexta: produto ou detalhe.
- Sábado: bastidor real, atendimento ou seleção da semana.
- Domingo: republicação de resposta, comentário ou prova real, somente com autorização.

Os horários iniciais da fila são testes. Após sete dias, manter os horários que gerarem mais visitas ao perfil, cliques e conversas, em vez de escolher apenas pelo número de curtidas.

## Rotina diária de relacionamento

1. Responder comentários e mensagens em duas janelas do dia.
2. Fazer uma pergunta específica quando a pessoa demonstrar interesse.
3. Enviar ao WhatsApp apenas quando a conversa pedir valores, disponibilidade ou atendimento.
4. Registrar a origem da conversa como `Instagram orgânico`, `Instagram Reel`, `TikTok orgânico` ou `Stories`.
5. Reunir perguntas recorrentes para criar o próximo conteúdo.

## Automação preparada

- Link da bio com UTM para separar tráfego orgânico.
- Respostas rápidas padronizadas no kit de perfil.
- Fila de publicações com data, ativo, legenda, objetivo e estado.
- Modelo de medição em `social/medicao.csv`.
- Código existente de mensagens do Instagram mantido como rascunho técnico; ele só pode ser considerado ativo após conexão e teste no ambiente da Meta.

## Próxima evolução dos criativos

O acervo atual é suficiente para iniciar. A próxima produção deve priorizar conteúdo que hoje não existe: pulso em movimento, troca de enquadramento em luz natural, embalagem real, escolha entre dois estilos e uma breve cena de atendimento. Novas imagens geradas devem complementar essas lacunas, sem substituir prova real quando ela for necessária.
