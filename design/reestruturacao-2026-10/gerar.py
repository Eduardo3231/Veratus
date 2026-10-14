"""Gera o index.html do protótipo a partir de landing/catalog.json.

Uso, na raiz do repositório:
    python3 -I design/reestruturacao-2026-10/gerar.py . design/reestruturacao-2026-10/index.html ../../landing/assets/ ../../landing/
Para levar ao ar depois da aprovação, os prefixos passam a ser "assets/" e "".
"""

import html
import json
import sys
from pathlib import Path
from urllib.parse import quote

repo, saida, ASSETS, PAGINAS = (
    Path(sys.argv[1]),
    Path(sys.argv[2]),
    sys.argv[3],
    sys.argv[4],
)
catalogo = json.loads((repo / "landing/catalog.json").read_text(encoding="utf-8"))

TELEFONE = "5511958323612"

# Centro do relógio em cada criativo (fração da largura e da altura), medido pela
# almofada preta dentro da caixa; platinum-classic e royal-blue conferidos a olho.
FOCO = {
    "arctic-white": (0.483, 0.566),
    "black-gmt": (0.494, 0.576),
    "bronze-heritage": (0.497, 0.546),
    "emerald-signature": (0.494, 0.580),
    "ocean-blue": (0.494, 0.563),
    "platinum-classic": (0.500, 0.600),
    "polar-blue": (0.492, 0.539),
    "royal-blue": (0.490, 0.515),
    "silver-prestige": (0.495, 0.564),
}


def referencia(fonte="site-direto"):
    """Mesmo FNV-1a de site.js: a referência da visita sem UTM."""
    h = 2166136261
    for c in fonte:
        h ^= ord(c)
        h = (h * 16777619) & 0xFFFFFFFF
    alfabeto = "0123456789abcdefghijklmnopqrstuvwxyz"
    s = ""
    while h:
        h, r = divmod(h, 36)
        s = alfabeto[r] + s
    return "VT-" + s.upper()


REF = referencia()
assert REF == "VT-RJ5MO2", REF  # o mesmo valor que o site do ar grava no HTML


def whatsapp(nome="", pid="", pedido=False):
    linhas = [
        f"Olá! Vim pelo site da Veratus e tenho interesse em {nome}."
        if nome
        else "Olá! Vim pelo site da Veratus e quero conhecer as coleções.",
        "Quero fazer o pedido e confirmar a disponibilidade."
        if pedido
        else "Gostaria de confirmar disponibilidade e valor.",
        f"Referência da visita: {REF}" + (f" | produto={pid}" if pid else ""),
    ]
    return f"https://wa.me/{TELEFONE}?text=" + quote(
        "\n".join(linhas), safe="-_.!~*'()"
    )


def e(texto):
    return html.escape(str(texto), quote=True)


def estilo(produto):
    """'Esportivo / Versátil' -> 'Esportivo, versátil'."""
    partes = [p.strip() for p in (produto.get("eyebrow") or "").split("/") if p.strip()]
    return ", ".join([partes[0]] + [p.lower() for p in partes[1:]]) if partes else ""


def preco(produto):
    inteiro, centavos = f"{float(produto['price_brl']):.2f}".split(".")
    return f"R$ {inteiro},{centavos}"


GERAL = e(whatsapp())

relogios = [p for p in catalogo if p.get("collection") == "watches"]
joias = {p["id"]: p for p in catalogo if p.get("collection") == "feminine"}

cartoes_relogio = []
for p in relogios:
    fx, fy = FOCO[p["id"]]
    link_vista = f"?relogio={p['id']}"
    cartoes_relogio.append(f"""
          <li class="relogio" data-id="{e(p["id"])}" data-estilos="{e(" ".join(p.get("styles", [])))}" data-nome="{e(p["name"])}" data-estilo="{e(estilo(p))}" data-descricao="{e(p["description"])}" data-preco="{e(preco(p))}" data-alt="{e(p["alt"])}" data-fx="{fx}" data-fy="{fy}">
            <a class="relogio__foto" href="{link_vista}" data-abrir tabindex="-1" aria-hidden="true">
              <img src="{e(ASSETS + p["image"].removeprefix("assets/"))}" alt="" width="1122" height="1402" loading="lazy" decoding="async">
            </a>
            <div class="relogio__info">
              <h3><a href="{link_vista}" data-abrir>{e(p["name"])}</a></h3>
              <p class="relogio__estilo">{e(estilo(p))}</p>
              <p class="relogio__preco">{e(preco(p))}</p>
              <div class="relogio__acoes">
                <a class="acao acao--ver" href="{link_vista}" data-abrir>Ver de perto</a>
                <a class="acao acao--pedir purchase-link" href="{e(whatsapp(p["name"], p["id"], True))}" target="_blank" rel="noopener" data-product-id="{e(p["id"])}" data-product-name="{e(p["name"])}" data-pedido="1" aria-label="Pedir o {e(p["name"])} pelo WhatsApp">Pedir</a>
              </div>
            </div>
          </li>""")

TIPO = {"necklace": "Colar", "bracelet": "Pulseira", "anklet": "Tornozeleira"}
TOM = {"silver_tone": "tom prateado", "gold_tone": "tom dourado"}


def cartao_joia(pid, destaque=False):
    p = joias[pid]
    classe = "joia joia--destaque" if destaque else "joia"
    tipo = f"{TIPO[p['subcategory']]}, {TOM[p['finish_color']]}"
    return f"""
            <li class="{classe}">
              <figure>
                <img src="{e(ASSETS + p["image"].removeprefix("assets/"))}" alt="{e(p["alt"])}" width="1122" height="1402" loading="lazy" decoding="async">
                <figcaption>
                  <h4>{e(p["name"])}</h4>
                  <p class="joia__tipo">{e(tipo)}</p>
                  <p class="joia__texto">{e(p.get("short_description") or p["description"])}</p>
                  <a class="acao acao--interesse purchase-link" href="{e(whatsapp(p["name"], p["id"]))}" target="_blank" rel="noopener" data-product-id="{e(p["id"])}" data-product-name="{e(p["name"])}" aria-label="Tenho interesse em {e(p["name"])}: abre o WhatsApp">Tenho interesse</a>
                </figcaption>
              </figure>
            </li>"""


GRUPOS = [
    (
        "colares",
        "Colares",
        ["halo-verde", "verde-aura", "rosa-aurea", "eclat-duo", "noir-clover"],
    ),
    ("pulseiras", "Pulseiras", ["lumiere", "celeste-link"]),
    ("tornozeleiras", "Tornozeleiras", ["celeste-anklet", "essenza-anklet"]),
]
assert sorted(pid for g in GRUPOS for pid in g[2]) == sorted(joias), (
    "toda joia do catálogo entra num grupo"
)


def grupo(chave, titulo, ids):
    cartoes = "".join(
        cartao_joia(pid, destaque=(i == 0 and len(ids) > 2))
        for i, pid in enumerate(ids)
    )
    pecas = f"{len(ids)} peças"
    return f"""
        <section class="joias__grupo joias__grupo--{chave}" aria-labelledby="grupo-{chave}">
          <div class="joias__grupo-topo"><h3 id="grupo-{chave}">{titulo}</h3><span>{pecas}</span></div>
          <ul class="joias__lista" role="list">{cartoes}
          </ul>
        </section>"""


grupos_html = "".join(grupo(*g) for g in GRUPOS)

# Mostrador do palco: 60 marcas de minuto, 12 maiores (lido em perspectiva no chão).
marcas = []
for i in range(60):
    longa = i % 5 == 0
    marcas.append(
        f'<line x1="100" y1="{4 if longa else 6}" x2="100" y2="{14 if longa else 10}" transform="rotate({i * 6} 100 100)" class="{"longa" if longa else "curta"}"/>'
    )
mostrador = "".join(marcas)

pagina = f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <meta name="robots" content="noindex">
  <meta name="description" content="Prévia da reestruturação do site Veratus. Não publicada.">
  <meta name="theme-color" content="#0d0d0d">
  <title>Veratus — prévia da reestruturação</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@400;500;600&family=Jost:wght@400;500;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="estilo.css">
  <!-- Prévia: sem o código do Meta Pixel. Ele volta igual ao do ar quando a página substituir landing/index.html. -->
</head>
<body>
  <a class="pular" href="#conteudo">Ir para o conteúdo</a>

  <header class="topo" id="topo">
    <a class="marca" href="#hero" aria-label="Veratus, início"><img src="{ASSETS}veratus-v-wheat-160.webp" alt="" width="180" height="180"><span>VERATUS</span></a>
    <nav class="topo__nav" aria-label="Navegação principal">
      <a href="#relogios">Relógios</a>
      <a href="#joias">Joias</a>
      <a class="topo__extra" href="#como-comprar">Como comprar</a>
    </nav>
    <a class="topo__conversar purchase-link" href="{GERAL}" target="_blank" rel="noopener">Conversar</a>
  </header>

  <main id="conteudo">
    <!-- Primeiro slide: mantido como está no ar (texto, símbolo e anéis). -->
    <section class="hero" id="hero" aria-labelledby="hero-title">
      <div class="hero-still" aria-hidden="true"></div>
      <div class="hero-veil" aria-hidden="true"></div>
      <div class="hero-layout shell">
        <div class="hero-copy">
          <p class="eyebrow"><span class="status-dot" aria-hidden="true"></span> Veratus / Tempo e presença</p>
          <h1 id="hero-title">O tempo<br><em>reconhece</em><br>você.</h1>
          <p>Relógios, joias e acessórios escolhidos para marcar presença.</p>
          <ul class="hero-offer" aria-label="Condições dos relógios"><li><b>R$ 289,90</b> cada relógio</li><li>Frete grátis</li><li>Entrega em até 7 dias</li></ul>
          <div class="hero-actions">
            <a class="button button-accent" href="#relogios">Ver os relógios</a>
            <a class="text-link purchase-link" href="{GERAL}" target="_blank" rel="noopener">Consultar <span aria-hidden="true">↗</span></a>
          </div>
        </div>
        <div class="brand-universe" aria-label="Símbolo original Veratus com anéis em movimento">
          <div class="brand-universe__halo" aria-hidden="true"></div>
          <div class="brand-universe__ring brand-universe__ring--outer" aria-hidden="true"></div>
          <div class="brand-universe__ring brand-universe__ring--middle" aria-hidden="true"></div>
          <div class="brand-universe__ring brand-universe__ring--inner" aria-hidden="true"></div>
          <div class="brand-universe__mark"><img src="{ASSETS}veratus-v-wheat-720.webp" alt="Símbolo Veratus: V dourado com espiga de trigo" width="1254" height="1254"></div>
          <span class="brand-universe__caption">PRECISÃO · EQUILÍBRIO · PERMANÊNCIA</span>
        </div>
      </div>
      <div class="hero-footer shell"><span>VERATUS / 2026</span><a href="#relogios">Entre na experiência <i aria-hidden="true">↓</i></a></div>
    </section>

    <section class="capitulo relogios" id="relogios" aria-labelledby="relogios-titulo">
      <div class="shell">
        <div class="capitulo__topo">
          <h2 id="relogios-titulo">Relógios</h2>
          <div class="relogios__oferta">
            <p class="relogios__preco">R$ 289,90 <span>cada</span></p>
            <p>Frete grátis e entrega em até 7 dias.</p>
            <p>Abra um modelo para vê-lo girar.</p>
          </div>
        </div>
        <div class="relogios__filtros" role="group" aria-label="Filtrar relógios por estilo" data-filtros hidden>
          <button type="button" aria-pressed="true" data-filtro="todos">Todos</button>
          <button type="button" aria-pressed="false" data-filtro="classico">Clássicos</button>
          <button type="button" aria-pressed="false" data-filtro="esportivo">Esportivos</button>
          <button type="button" aria-pressed="false" data-filtro="marcante">Marcantes</button>
          <span class="relogios__contagem" aria-live="polite" data-contagem>9 modelos</span>
        </div>
        <ul class="relogios__grade" role="list">{"".join(cartoes_relogio)}
        </ul>
      </div>
    </section>

    <section class="capitulo joias" id="joias" aria-labelledby="joias-titulo">
      <div class="shell">
        <div class="capitulo__topo">
          <h2 id="joias-titulo">Joias</h2>
          <p class="joias__intro">Colares, pulseiras e tornozeleiras da coleção Veratus Feminino, em tom dourado e tom prateado. Disponibilidade e valor de cada peça são confirmados na conversa.</p>
        </div>{grupos_html}
      </div>
    </section>

    <section class="capitulo compra" id="como-comprar" aria-labelledby="compra-titulo">
      <div class="shell">
        <h2 id="compra-titulo">Como comprar</h2>
        <ol class="compra__passos">
          <li><h3>Escolha</h3><p>Abra o relógio ou a joia que chamou sua atenção.</p></li>
          <li><h3>Converse</h3><p>O WhatsApp abre com a peça escolhida e a referência da sua visita.</p></li>
          <li><h3>Confirme</h3><p>A equipe confirma disponibilidade, valor e forma de pagamento antes do pedido.</p></li>
          <li><h3>Receba</h3><p>Frete grátis e entrega em até 7 dias, sem taxas adicionais.</p></li>
        </ol>
        <p class="compra__nota">Você pode desistir em até 7 dias após o recebimento. <a href="{PAGINAS}condicoes-de-compra.html">Condições de compra</a></p>
      </div>
    </section>
  </main>

  <footer class="rodape">
    <div class="shell">
      <div class="rodape__chamada">
        <p>Você faz parte do tempo.</p>
        <a class="botao botao--ouro purchase-link" href="{GERAL}" target="_blank" rel="noopener">Conversar com a Veratus</a>
      </div>
      <div class="rodape__base">
        <a class="marca" href="#hero"><img src="{ASSETS}veratus-v-wheat-160.webp" alt="" width="180" height="180"><span>VERATUS</span></a>
        <address class="rodape__vendedor" aria-label="Identificação do vendedor"><b>Veratus</b><span>São Paulo/SP</span><a href="mailto:veratus.ltda@gmail.com">veratus.ltda@gmail.com</a><a class="purchase-link" href="{GERAL}" target="_blank" rel="noopener">(11) 95832-3612</a><!--seller-document--></address>
        <nav aria-label="Links do rodapé"><a href="https://www.instagram.com/veratus_oficial/" target="_blank" rel="noopener">Instagram</a><a href="{PAGINAS}privacy.html">Privacidade</a><a href="{PAGINAS}condicoes-de-compra.html">Condições de compra</a></nav>
        <small>© <span data-ano>2026</span> Veratus</small>
      </div>
    </div>
  </footer>

  <div class="barra-movel" data-barra-movel>
    <p>Relógios a <b>R$ 289,90</b></p>
    <a class="botao botao--ouro purchase-link" href="{GERAL}" target="_blank" rel="noopener">Conversar</a>
  </div>

  <dialog class="vista" id="vista" aria-labelledby="vista-nome">
    <div class="vista__topo">
      <button class="vista__voltar" type="button" data-fechar>Voltar à coleção</button>
      <p class="vista__posicao" data-posicao></p>
    </div>
    <figure class="palco" data-palco>
      <div class="palco__ambiente" aria-hidden="true"></div>
      <div class="palco__cena">
        <div class="palco__chao" aria-hidden="true">
          <svg class="palco__mostrador" viewBox="0 0 200 200"><circle cx="100" cy="100" r="97"/>{mostrador}<circle class="pip" cx="100" cy="23" r="3.2"/></svg>
        </div>
        <div class="palco__entrada">
          <div class="palco__giro">
            <span class="palco__borda palco__borda--esquerda" aria-hidden="true"></span>
            <span class="palco__borda palco__borda--direita" aria-hidden="true"></span>
            <div class="palco__painel">
              <img class="palco__foto" src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" alt="" width="1122" height="1402">
              <span class="palco__reflexo" aria-hidden="true"></span>
            </div>
          </div>
        </div>
      </div>
      <p class="palco__dica" aria-hidden="true">Role para girar</p>
      <figcaption class="palco__legenda">Imagem de apresentação do modelo</figcaption>
    </figure>
    <div class="vista__texto">
      <section class="vista__bloco vista__bloco--abertura">
        <p class="vista__estilo" data-estilo></p>
        <h2 id="vista-nome" data-nome tabindex="-1" autofocus>Relógio</h2>
        <p class="vista__descricao" data-descricao></p>
        <p class="vista__preco"><b data-preco></b><span>Frete grátis e entrega em até 7 dias, sem taxas adicionais.</span></p>
        <a class="botao botao--ouro purchase-link" href="{GERAL}" target="_blank" rel="noopener" data-pedir>Pedir pelo WhatsApp</a>
      </section>
      <section class="vista__bloco" aria-labelledby="vista-passos">
        <h3 id="vista-passos">Como o pedido funciona</h3>
        <ol class="vista__passos">
          <li>O WhatsApp abre com o modelo e a referência da sua visita.</li>
          <li>A equipe confirma disponibilidade, valor e forma de pagamento antes do pedido.</li>
          <li>Você recebe em até 7 dias, com frete grátis.</li>
        </ol>
        <p class="vista__nota">Você pode desistir em até 7 dias após o recebimento. <a href="{PAGINAS}condicoes-de-compra.html">Condições de compra</a></p>
      </section>
      <section class="vista__bloco" aria-labelledby="vista-outros">
        <h3 id="vista-outros">Outros modelos</h3>
        <ul class="vista__outros" role="list" data-outros></ul>
      </section>
    </div>
    <div class="vista__barra">
      <p><b data-preco></b><span>Frete grátis</span></p>
      <a class="botao botao--ouro purchase-link" href="{GERAL}" target="_blank" rel="noopener" data-pedir>Pedir pelo WhatsApp</a>
    </div>
  </dialog>

  <script src="vitrine.js"></script>
</body>
</html>
"""

saida.parent.mkdir(parents=True, exist_ok=True)
saida.write_text(pagina, encoding="utf-8")
print(
    f"{saida}: {len(pagina)} bytes, {len(relogios)} relógios, {len(joias)} joias, referência {REF}"
)
