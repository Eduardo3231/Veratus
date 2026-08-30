# Veratus — Subida do Produto

## Estado da operação

- **Site público atual:** `https://veratus.onrender.com/` ainda exibe acesso antecipado.
- **Página de venda preparada localmente:** `landing/index.html`.
- **Canal de compra:** WhatsApp `+55 11 95832-3612`.
- **Oferta informada:** a partir de R$ 389,90 por unidade.
- **Prazo informado:** entrega para todo o Brasil em até 7 dias após confirmação.
- **Anúncios:** somente prévias; nenhuma campanha, anúncio ou gasto foi criado.

## O que a página de venda entrega

1. Dois botões de WhatsApp com mensagem pré-preenchida.
2. Preço, alcance de entrega e prazo em linguagem clara.
3. Atendimento antes da confirmação do pedido, para validar modelo e cidade.
4. Texto comercial sem prometer especificações, garantia, estoque ou condição que não tenha sido confirmada para o modelo escolhido.

## Publicação do site

1. Revise no VS Code as mudanças em `landing/index.html` e `landing/styles.css`.
2. Faça o commit e envie para a branch `main`.
3. Aguarde o deploy do Render terminar.
4. Abra `https://veratus.onrender.com/` e valide:
   - os botões abrem uma conversa com `+55 11 95832-3612`;
   - aparece o preço a partir de R$ 389,90;
   - aparece o prazo de até 7 dias;
   - a página não menciona mais “acesso antecipado”.

Comandos sugeridos no terminal do VS Code, depois de revisar as alterações:

```powershell
git add landing/index.html landing/styles.css
git commit -m "feat: preparar pagina de venda via WhatsApp"
git push origin main
```

Não inclua `leads.csv` em novos commits: ele pode conter contatos de clientes.

## Atendimento de compra

Mensagem automática recebida pelo cliente:

> Olá, vim pelo site da Veratus e quero comprar um relógio. Pode me mostrar os modelos disponíveis?

Resposta inicial recomendada:

> Olá! Seja bem-vindo à Veratus. Claro — me diga qual modelo chamou sua atenção e sua cidade. Eu confirmo disponibilidade, valor final e prazo de envio antes de você concluir o pedido.

Antes de receber pagamento, confirme por mensagem: modelo, cor, valor total, endereço, prazo, forma de envio e política de troca aplicável.

## Anúncios — rascunho pronto para revisão

**Destino:** `https://veratus.onrender.com/`

**Imagem:** `C:\Users\PC GAMER\Downloads\ Rolex (2).png`

**Orçamento proposto:** R$ 50/dia

**Abrangência proposta:** Brasil
**Status inicial recomendado:** pausado

| Variação | Título | Texto |
| --- | --- | --- |
| 1 | Presença no pulso | Modelos selecionados. Atendimento direto pelo WhatsApp. |
| 2 | Escolha de presença | Detalhes que finalizam o visual. Atendimento pelo WhatsApp. |
| 3 | Seu tempo, seu estilo | Conheça os modelos disponíveis. |
| 4 | Elegância nos detalhes | Seleção masculina. Entrega para todo o Brasil. |
| 5 | Um detalhe que fica | A partir de R$389,90. Envio em até 7 dias. |

## Liberação de mídia

Só criar e ativar os anúncios depois de confirmar estes itens:

- A página de venda está pública e os botões funcionam.
- A conta de anúncios está identificada visualmente como **Veratus**; no momento ela aparece como **N.C**.
- A imagem usada representa corretamente o modelo que será oferecido.
- Você consegue responder rapidamente no WhatsApp durante a campanha.
- A disponibilidade, o prazo e a entrega informados no anúncio podem ser cumpridos.

## Próxima ação

Quando voltar, publique o site ou me autorize a preparar o commit. Depois da confirmação da página pública, apresento novamente as prévias e, com sua aprovação explícita, crio a campanha pausada de R$ 50/dia para revisão final.
