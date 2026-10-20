# Criativos estáticos das correntes

Modelos de propaganda na família da arte de referência do fundador (preto profundo, dourado,
emblema circular, peça em destaque com quadros de detalhe e faixa de vantagens), com as
fotos de produto e só fatos `CONFIRMED`.

| Arquivo | Formato | Peça |
|---|---|---|
| `veratus-classico.png` | feed 4:5 (1080x1350) | Corrente no busto, "Clássico é sempre atual.", fecho e elo de perto |
| `veratus-simbolo.png` | feed 4:5 | Pingente crucifixo, na corrente e na palma da mão |
| `veratus-presenca.png` | feed 4:5 | Pingente do rosto, "Presença em cada detalhe" |
| `veratus-elo.png` | feed 4:5 | Corrente deitada, "Elo a elo." |
| `veratus-story.png` | story 9:16 (1080x1920) | Clássico com o botão do WhatsApp |

Da referência saíram as frases que a regra da marca bloqueia ("prata 925", "qualidade
comprovada", "brilho duradouro", "alta durabilidade", "embalagem premium"); no lugar entram
frete grátis, entrega em até 7 dias, nenhuma taxa extra, desistência em até 7 dias, Pix ou
link do Mercado Pago e atendimento no WhatsApp. Ícones desenhados em traço único (sem logotipo
do WhatsApp nem do Pix). O emblema é o círculo recortado de `landing/assets/veratus-emblem.jpeg`,
sem alteração.

Gerar: monte `runtime/criativos/correntes/` com `fotos/`, `fontes/` (as mesmas do vídeo),
`emblema-circular.png` e `emblema.png`, e rode `python criativos/correntes/render.py`.
`tests/test_video_correntes.py` passa todo texto visível de `modelos.html` pelo filtro do
cliente. Publicar só depois de `approve-media` e da aprovação do fundador.
