# Vídeo do sniper (9:16, 35 s)

Pedido do fundador (24/10/2026): um atirador de elite numa montanha dá um tiro que se ouve a
quilômetros; a câmera segue a bala até o alvo e, quanto mais perto, mais devagar ela anda.
Depois do impacto, "Você viu como esse vídeo te prendeu?" vira o gancho para apresentar a
Veratus. É a mesma ideia da esfera do vídeo das correntes: tensão sem marca, estouro, produto.

## Roteiro

| Tempo | Cena | O que prende |
|---|---|---|
| 0,0–2,2 s | Atirador deitado no alto do penhasco, pôr do sol; o alvo é um brilho do outro lado do vale. "1.250 METROS." | Escala. Nada de marca nem produto. |
| 2,2–3,6 s | O alvo: ampulheta de vidro com areia dourada num pedestal de pedra. "UM ALVO." | Curiosidade: por que uma ampulheta? (tempo é atenção) |
| 3,6–5,8 s | Pela luneta: foco, respiração, coração e quatro cliques de ajuste do vento. "UM TIRO." De 5,45 s ao disparo, silêncio total. | O espectador prende a respiração junto. |
| 5,8–6,6 s | O disparo: clarão, onda de choque, fumaça, pássaros saindo das árvores; o som rola pelas montanhas em seis ecos. | O estouro. |
| 6,6–12,2 s | A câmera segue a bala por trás, com DISTÂNCIA e VELOCIDADE na tela. "SIGA A BALA." · "QUANTO MAIS PERTO…" | Velocidade real até uns 550 m; dali a velocidade é proporcional ao que falta, e a distância cai dez vezes a cada 1,5 s (1.000 m, 100 m, 10 m, 1 m, 10 cm, 1 cm). O assobio desce de tom, o coração desacelera e um tique marca cada queda. |
| 12,2–15,6 s | Câmera lenta de lado: a bala gira devagar, as estrias aparecem, o vidro entra no quadro. "…MAIS DEVAGAR." | Os últimos 4,5 cm levam 2 s. |
| 15,6–16,6 s | Contato: o vidro trinca, os cacos soltam, a areia sai. | |
| 16,6–20,0 s | Tudo congela; o som corta e fica só o vidro vibrando. "Você viu como esse vídeo te prendeu?" · "Uma boa peça faz o mesmo com quem olha para você." | A virada para a marca. |
| 20,0–22,0 s | A areia dourada forma o V da Veratus. "VERATUS · Correntes e pingentes." | |
| 22,0–26,6 s | Três tomadas das correntes, com cantoneiras de mira. | Elo a elo · Pingentes que completam · Cada detalhe, de perto |
| 26,6–30,6 s | Como a Veratus trabalha, só com fatos `CONFIRMED`. | WhatsApp com o Harvey (assistente virtual) · Frete grátis e nenhuma taxa extra · Entrega em até 7 dias · Desistência em até 7 dias após o recebimento · Pix ou link do Mercado Pago |
| 30,6–35,0 s | Emblema dentro da mira, "Qual peça está na sua mira?" e o botão do WhatsApp (11) 95832-3612. | |

Textos e tempos ficam em `roteiro.json`. `tests/test_video_sniper.py` passa cada texto pelo
filtro das respostas ao cliente, barra prata, ouro, garantia, urgência e marca de arma ou de
munição, e confere que a marca só aparece depois do congelamento.

**O alvo é trocável.** A ampulheta foi escolhida porque conversa com o gancho (tempo, atenção) e
porque a areia dourada vira o emblema. Outras opções: a esfera preta do vídeo das correntes
(vira série), uma taça de cristal. Atirar numa peça ou num porta-joias fica de fora: passa a ideia
de resistência, que não é fato confirmado.

## Como gerar

1. Em `runtime/video/sniper/` (fora do Git) coloque `fontes/` com os WOFF2 da Cormorant Garamond
   (500, 600, 500 itálico e 600 itálico) e da Jost (400, 500, 600), licença OFL
   (`npm i @fontsource/cormorant-garamond @fontsource/jost`), e `fotos/` com
   `corrente-busto-frente`, `crucifixo`, `corrente-plana` e `corrente-busto-angulo` (PNG), as
   mesmas do vídeo das correntes.
2. `python video/sniper/render.py` (Chromium com Playwright e ffmpeg; 4 processos em paralelo).
   `--previa` roda sem as fotos, com quadros marcados no lugar delas; `--quadros 5.85,16.8` gera
   só PNGs. Se o Playwright do Python não tiver o próprio navegador, aponte
   `CHROMIUM_EXECUTABLE` para o Chromium instalado.
3. O som sai de `audio.py` (ondas e ruído sintetizados, sem amostra de terceiros).

A abertura inteira (atirador, alvo, luneta, tiro, bala, vidro e areia) é desenhada no
`cena.html`: não usa foto, vídeo nem marca de terceiros, e a arma não tem marca.

## Antes de publicar

- Aprovação do fundador, `approve-media` e aprovação no agendador, como qualquer mídia.
- As fotos são do fornecedor, com autorização declarada em 19/10: guardar por escrito.
- "Entrega em até 7 dias" ainda depende do prazo das joias de revenda (mesma pendência do vídeo
  das correntes).
- **Anúncio pago:** arma e tiro na tela costumam ser barrados na revisão de anúncios da Meta e do
  TikTok. Este corte é para Reels, Stories e TikTok orgânicos; para impulsionar, fazer uma versão
  sem arma nem munição.
- **Versão realista** (opcional): o conector Higgsfield gera vídeo, mas gasta créditos pagos;
  só com confirmação, e alguns modelos recusam arma. As cenas descritas acima servem de prompt,
  uma por tomada, em 9:16.
