# Vídeo das correntes (9:16, 25,5 s)

Versão nova do vídeo de propaganda, só com correntes e pingentes. O vídeo antigo está na
quarentena (marca de terceiros) e não é reaproveitado.

## Roteiro

| Tempo | Cena | O que prende |
|---|---|---|
| 0,0–4,8 s | **Tensão**: uma esfera preta racha, a contagem 5-4-3-2-1 acelera, o tique-taque sobe, a tela treme | Nada de Veratus nem de produto: o espectador espera o estouro, como no balão. Sem preço, sem oferta, sem prazo. |
| 4,8–13,0 s | **Interesse**: o estouro vira a primeira foto; quatro tomadas com título curto | Elo a elo · Pingentes que completam · Cada detalhe, de perto · Peças com presença |
| 13,0–16,2 s | **Diferença**: só fatos `CONFIRMED` de `knowledge/fatos.yaml` | Frete grátis · Nenhuma taxa extra · Entrega em até 7 dias · Desistência em até 7 dias após o recebimento · Pix ou link do Mercado Pago |
| 16,2–21,0 s | **Resposta**: a conversa com o Harvey (assistente virtual, cliente fictício) | O cliente manda a foto e pergunta; o Harvey responde e confirma valor e disponibilidade na conversa |
| 21,0–25,5 s | **Fecho**: emblema, "Qual elo é o seu?" e o botão do WhatsApp | Pergunta que convida a comentar; chamada para (11) 95832-3612 |

Os textos e os tempos ficam em `roteiro.json`. `tests/test_video_correntes.py` passa cada texto
pelo filtro das respostas ao cliente e barra "prata", "925", "ouro", "banho", garantia,
urgência e escassez.

## Como gerar

1. Em `runtime/video/correntes/` (fora do Git) coloque `fotos/` com `corrente-busto-frente`,
   `corrente-busto-angulo`, `corrente-plana`, `crucifixo` e `rosto` (PNG) e `fontes/` com os WOFF2
   da Cormorant Garamond (500, 600 e 500 itálico) e da Jost (400, 500, 600), licença OFL
   (`npm i @fontsource/cormorant-garamond @fontsource/jost`).
2. `python video/correntes/render.py` (Chromium com Playwright e ffmpeg; 3 processos em paralelo).
   `--quadros 0.5,4.9` gera só PNGs para conferir o desenho.
3. O som sai de `audio.py` (ondas e ruído sintetizados, sem amostra de terceiros).

## Antes de publicar

- As fotos vêm do fornecedor, com autorização declarada pelo fundador em 19/10/2026: guardar a
  autorização por escrito. Elas não estão no Git.
- Nada publica sozinho: o arquivo precisa de `approve-media` e da aprovação do fundador no
  agendador, como qualquer mídia.
