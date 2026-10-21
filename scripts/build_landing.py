"""Build landing/index.html from the public catalogue (landing/catalog.json).

The page is static, so every product, price and WhatsApp link is visible
without JavaScript; site.js only adds the visit reference with UTMs, the watch
view and the Meta Pixel events. export_public_catalog.py runs this after every
export, and tests/test_landing_resilience.py fails when page and catalogue
drift apart.

    python scripts/build_landing.py
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "landing" / "catalog.json"
OUTPUT = ROOT / "landing" / "index.html"
PHONE = "5511958323612"

# Three groups of three: a watch with two styles goes to the group named here,
# any other watch to its first style.
WATCH_GROUPS = (
    ("classico", "Clássicos"),
    ("esportivo", "Esportivos"),
    ("marcante", "Marcantes"),
)
PRIMARY_STYLE = {"bronze-heritage": "marcante"}

JEWELRY_GROUPS = (
    ("chain", "Correntes"),
    ("necklace", "Colares"),
    ("bracelet", "Pulseiras"),
    ("anklet", "Tornozeleiras"),
)
JEWELRY_TYPE = {
    "chain": "Corrente",
    "necklace": "Colar",
    "bracelet": "Pulseira",
    "anklet": "Tornozeleira",
}
JEWELRY_TONE = {"silver_tone": "tom prateado", "gold_tone": "tom dourado"}
# Off the storefront while its resemblance alert is open (docs/HANDOFF.md).
ON_HOLD = {"noir-clover"}

# Centre of the watch inside each catalogue creative (share of width and
# height), for the round thumbnails of the watch view. Real photos are centred.
CREATIVE_FOCUS = {
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


def visit_reference(source: str = "site-direto") -> str:
    """campaignReference() of site.js (FNV-1a, base 36) for a visit without UTMs."""
    value = 2166136261
    for character in source:
        value = ((value ^ ord(character)) * 16777619) & 0xFFFFFFFF
    digits, reference = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ", ""
    while value:
        value, remainder = divmod(value, 36)
        reference = digits[remainder] + reference
    return f"VT-{reference}"


def whatsapp_link(name: str = "", product_id: str = "", order: bool = False) -> str:
    """createWhatsAppLink() of site.js, so the link works before the script runs."""
    lines = [
        f"Olá! Vim pelo site da Veratus e tenho interesse em {name}."
        if name
        else "Olá! Vim pelo site da Veratus e quero conhecer as coleções.",
        "Quero fazer o pedido e confirmar a disponibilidade."
        if order
        else "Gostaria de confirmar disponibilidade e valor.",
        f"Referência da visita: {visit_reference()}"
        + (f" | produto={product_id}" if product_id else ""),
    ]
    text = quote("\n".join(lines), safe="-_.!~*'()")
    return f"https://wa.me/{PHONE}?text={text}"


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def _price(value: str) -> str:
    """'1299.90' -> 'R$ 1.299,90', with a non-breaking space after R$."""
    amount = f"{float(value):,.2f}".translate(str.maketrans(",.", ".,"))
    return f"R$\u00a0{amount}"


def _style_line(product: dict) -> str:
    """'Esportivo / Versátil' -> 'Esportivo, versátil'."""
    parts = [p.strip() for p in (product.get("eyebrow") or "").split("/") if p.strip()]
    return ", ".join(parts[:1] + [part.lower() for part in parts[1:]])


def _watch_card(product: dict) -> str:
    fx, fy = CREATIVE_FOCUS.get(product["id"], (0.5, 0.5))
    if not product["image"].startswith("assets/catalog/"):
        fx, fy = 0.5, 0.5
    view = f"?relogio={product['id']}"
    name = _e(product["name"])
    return f"""
            <li class="relogio" data-item data-product-id="{_e(product["id"])}" data-name="{name}" data-style="{_e(_style_line(product))}" data-description="{_e(product["description"])}" data-price="{_e(_price(product["price_brl"]))}" data-value="{_e(product["price_brl"])}" data-alt="{_e(product["alt"])}" data-fx="{fx}" data-fy="{fy}">
              <a class="relogio__foto" href="{view}" data-open tabindex="-1" aria-hidden="true">
                <img src="{_e(product["image"])}" alt="" aria-hidden="true" width="1122" height="1402" loading="lazy" decoding="async">
              </a>
              <div class="relogio__info">
                <h4><a href="{view}" data-open>{name}</a></h4>
                <p class="relogio__estilo">{_e(_style_line(product))}</p>
                <p class="relogio__preco">{_price(product["price_brl"])}</p>
                <div class="relogio__acoes">
                  <a class="acao acao--ver" href="{view}" data-open>Ver de perto</a>
                  <a class="acao acao--pedir purchase-link" href="{_e(whatsapp_link(product["name"], product["id"], order=True))}" target="_blank" rel="noopener" data-product-id="{_e(product["id"])}" data-product-name="{name}" data-order="1" aria-label="Pedir o {name} pelo WhatsApp">Pedir</a>
                </div>
              </div>
            </li>"""


def _group(prefix: str, key: str, title: str, cards: str, count: int, unit: str) -> str:
    wide = " grupo--largo" if count > 2 else ""
    return f"""
        <section class="grupo{wide}" id="{prefix}-{key}" aria-labelledby="{prefix}-{key}-titulo">
          <div class="grupo__topo"><h3 id="{prefix}-{key}-titulo">{title}</h3><span>{count} {unit}</span></div>
          <ul class="{prefix}__lista" role="list">{cards}
          </ul>
        </section>"""


def _jewel_card(product: dict) -> str:
    # Only "tom dourado" or "tom prateado" describe a piece; any other finish
    # stays unnamed until the founder words it.
    tone = JEWELRY_TONE.get(product.get("finish_color"))
    kind = ", ".join(filter(None, [JEWELRY_TYPE[product["subcategory"]], tone]))
    name = _e(product["name"])
    return f"""
            <li class="joia" data-item data-product-id="{_e(product["id"])}" data-name="{name}">
              <figure>
                <img src="{_e(product["primary_image"])}" alt="{_e(product["alt"])}" width="1122" height="1402" loading="lazy" decoding="async">
                <figcaption>
                  <h4>{name}</h4>
                  <p class="joia__tipo">{_e(kind)}</p>
                  <p class="joia__texto">{_e(product.get("short_description") or product["description"])}</p>
                  <a class="acao acao--interesse purchase-link" href="{_e(whatsapp_link(product["name"], product["id"]))}" target="_blank" rel="noopener" data-product-id="{_e(product["id"])}" data-product-name="{name}" aria-label="Tenho interesse em {name}: abre o WhatsApp">Tenho interesse</a>
                </figcaption>
              </figure>
            </li>"""


def _dial_marks() -> str:
    """Sixty minute marks, twelve long ones, for the dial under the watch view."""
    marks = []
    for minute in range(60):
        long_mark = minute % 5 == 0
        css_class = ' class="longa"' if long_mark else ""
        marks.append(
            f'<line x1="100" y1="{4 if long_mark else 6}" x2="100" '
            f'y2="{14 if long_mark else 10}" '
            f'transform="rotate({minute * 6} 100 100)"{css_class}/>'
        )
    return "".join(marks)


def render(catalog: list[dict]) -> str:
    watches = [item for item in catalog if item.get("collection") == "watches"]
    jewelry = [
        item
        for item in catalog
        if item.get("collection") == "feminine"
        and item.get("site_visibility") == "PREVIEW"
        and item["id"] not in ON_HOLD
    ]

    watch_groups, grouped = [], 0
    for style, title in WATCH_GROUPS:
        members = [
            item
            for item in watches
            if PRIMARY_STYLE.get(item["id"], (item.get("styles") or [""])[0]) == style
        ]
        if not members:
            continue
        grouped += len(members)
        cards = "".join(_watch_card(item) for item in members)
        unit = "modelo" if len(members) == 1 else "modelos"
        watch_groups.append((style, title, cards, len(members), unit))
    if grouped != len(watches):
        raise ValueError("todo relógio do catálogo precisa entrar num grupo")

    jewel_groups, grouped = [], 0
    for kind, title in JEWELRY_GROUPS:
        members = [item for item in jewelry if item.get("subcategory") == kind]
        if not members:
            continue
        grouped += len(members)
        cards = "".join(_jewel_card(item) for item in members)
        unit = "peça" if len(members) == 1 else "peças"
        jewel_groups.append((kind, title, cards, len(members), unit))
    if grouped != len(jewelry):
        raise ValueError("toda joia publicada precisa entrar num grupo")

    prices = sorted({float(item["price_brl"]) for item in watches})
    lowest = _price(str(prices[0]))
    if len(prices) == 1:
        hero_price = f"<b>{lowest}</b> cada relógio"
        header_price = f"{lowest} <span>cada</span>"
        bar_price = f"Relógios a <b>{lowest}</b>"
    else:
        hero_price = f"A partir de <b>{lowest}</b>"
        header_price = f"<span>a partir de</span> {lowest}"
        bar_price = f"Relógios a partir de <b>{lowest}</b>"
    index = "".join(
        f'<a href="#relogios-{style}">{title} <span>{count}</span></a>'
        for style, title, _, count, _ in watch_groups
    )
    watch_html = "".join(_group("relogios", *group) for group in watch_groups)
    jewel_html = "".join(_group("joias", *group) for group in jewel_groups)
    general = _e(whatsapp_link())
    return PAGE.format(
        general=general,
        hero_price=hero_price,
        header_price=header_price,
        bar_price=bar_price,
        index=index,
        watch_groups=watch_html,
        jewel_groups=jewel_html,
        dial=_dial_marks(),
    )


PAGE = """<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <meta name="description" content="Veratus — relógios, joias e acessórios criados para acompanhar a sua presença.">
  <meta name="theme-color" content="#0d0d0d">
  <meta property="og:title" content="Veratus — Você faz parte do tempo">
  <meta property="og:description" content="Explore as coleções Veratus em uma experiência visual e interativa.">
  <meta property="og:image" content="https://veratus.onrender.com/assets/og/veratus-og-1200x630.jpg">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  <meta property="og:image:alt" content="Símbolo Veratus: V dourado com espiga de trigo">
  <meta property="og:url" content="https://veratus.onrender.com/">
  <meta property="og:type" content="website">
  <meta property="og:locale" content="pt_BR">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:image" content="https://veratus.onrender.com/assets/og/veratus-og-1200x630.jpg">
  <meta name="twitter:image:alt" content="Símbolo Veratus: V dourado com espiga de trigo">
  <link rel="canonical" href="https://veratus.onrender.com/">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@400;500;600&family=Jost:wght@400;500;700&display=swap" rel="stylesheet">
  <link rel="icon" href="assets/favicon-64.png?v=5" type="image/png" sizes="64x64">
  <link rel="apple-touch-icon" href="assets/apple-touch-icon.png?v=4">
  <link rel="stylesheet" href="styles.css">
  <title>Veratus — Você faz parte do tempo</title>
  <!-- Meta Pixel Code -->
  <script>
  // Visitas locais e testes automáticos não entram nas métricas do Pixel.
  if (!/^(localhost|127\\.|0\\.0\\.0\\.0|\\[::1\\])/.test(location.hostname) && location.protocol !== 'file:') {{
  !function(f,b,e,v,n,t,s)
  {{if(f.fbq)return;n=f.fbq=function(){{n.callMethod?
  n.callMethod.apply(n,arguments):n.queue.push(arguments)}};
  if(!f._fbq)f._fbq=n;n.push=n;n.loaded=!0;n.version='2.0';
  n.queue=[];t=b.createElement(e);t.async=!0;
  t.src=v;s=b.getElementsByTagName(e)[0];
  s.parentNode.insertBefore(t,s)}}(window, document,'script',
  'https://connect.facebook.net/en_US/fbevents.js');
  fbq('init', '1633870688525258');
  fbq('track', 'PageView');
  }}
  </script>
  <!-- End Meta Pixel Code -->
</head>
<body>
  <noscript><img height="1" width="1" style="display:none" alt="" aria-hidden="true" src="https://www.facebook.com/tr?id=1633870688525258&amp;ev=PageView&amp;noscript=1"></noscript>
  <a class="pular" href="#conteudo">Ir para o conteúdo</a>

  <header class="topo" id="topo">
    <a class="marca" href="#hero" aria-label="Veratus, início"><img src="assets/veratus-v-wheat-160.webp" alt="" aria-hidden="true" width="180" height="180"><span>VERATUS</span></a>
    <nav class="topo__nav" aria-label="Navegação principal">
      <a href="#relogios">Relógios</a>
      <a href="#joias">Joias</a>
      <a class="topo__extra" href="#como-comprar">Como comprar</a>
    </nav>
    <a class="topo__conversar purchase-link" href="{general}" target="_blank" rel="noopener">Conversar</a>
  </header>

  <main id="conteudo">
    <section class="hero" id="hero" aria-labelledby="hero-title">
      <div class="hero-still" aria-hidden="true"></div>
      <div class="hero-veil" aria-hidden="true"></div>
      <div class="hero-layout shell">
        <div class="hero-copy">
          <p class="eyebrow"><span class="status-dot" aria-hidden="true"></span> Veratus / Tempo e presença</p>
          <h1 id="hero-title">O tempo<br><em>reconhece</em><br>você.</h1>
          <p>Relógios, joias e acessórios escolhidos para marcar presença.</p>
          <ul class="hero-offer" aria-label="Condições dos relógios"><li>{hero_price}</li><li>Frete grátis</li><li>Entrega em até 7 dias</li></ul>
          <div class="hero-actions">
            <a class="button button-accent" href="#relogios">Ver os relógios</a>
            <a class="text-link purchase-link" href="{general}" target="_blank" rel="noopener">Consultar <span aria-hidden="true">↗</span></a>
          </div>
        </div>
        <div class="brand-universe" aria-label="Símbolo original Veratus com anéis em movimento">
          <div class="brand-universe__halo" aria-hidden="true"></div>
          <div class="brand-universe__ring brand-universe__ring--outer" aria-hidden="true"></div>
          <div class="brand-universe__ring brand-universe__ring--middle" aria-hidden="true"></div>
          <div class="brand-universe__ring brand-universe__ring--inner" aria-hidden="true"></div>
          <div class="brand-universe__mark"><img src="assets/veratus-v-wheat-720.webp" alt="Símbolo Veratus: V dourado com espiga de trigo" width="1254" height="1254"></div>
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
            <p class="relogios__preco">{header_price}</p>
            <p>Frete grátis e entrega em até 7 dias.</p>
            <p>Abra um modelo para vê-lo girar.</p>
          </div>
        </div>
        <nav class="capitulo__indice" aria-label="Tipos de relógio">{index}</nav>{watch_groups}
      </div>
    </section>

    <section class="capitulo joias" id="joias" aria-labelledby="joias-titulo">
      <div class="shell">
        <div class="capitulo__topo">
          <h2 id="joias-titulo">Joias</h2>
          <p class="joias__intro">Correntes, colares, pulseiras e tornozeleiras em tom dourado e tom prateado. Disponibilidade e valor de cada peça são confirmados na conversa.</p>
        </div>
        <div class="joias__grupos">{jewel_groups}
        </div>
      </div>
    </section>

    <section class="capitulo compra" id="como-comprar" aria-labelledby="compra-titulo">
      <div class="shell">
        <h2 id="compra-titulo">Como comprar</h2>
        <ol class="compra__passos">
          <li><span class="compra__numero">1</span><h3>Escolha</h3><p>Abra o relógio ou a joia que chamou sua atenção.</p></li>
          <li><span class="compra__numero">2</span><h3>Converse</h3><p>O WhatsApp abre com a peça escolhida e a referência da sua visita.</p></li>
          <li><span class="compra__numero">3</span><h3>Confirme</h3><p>A equipe confirma disponibilidade, valor e forma de pagamento antes do pedido.</p></li>
          <li><span class="compra__numero">4</span><h3>Receba</h3><p>Frete grátis e entrega em até 7 dias, sem taxas adicionais.</p></li>
        </ol>
        <p class="compra__nota">Você pode desistir em até 7 dias após o recebimento. <a href="condicoes-de-compra.html">Condições de compra</a></p>
      </div>
    </section>
  </main>

  <footer class="rodape" id="contato">
    <div class="shell">
      <div class="rodape__chamada">
        <p>Você faz parte do tempo.</p>
        <a class="botao botao--ouro purchase-link" href="{general}" target="_blank" rel="noopener">Conversar com a Veratus</a>
      </div>
      <div class="rodape__base">
        <a class="marca" href="#hero"><img src="assets/veratus-v-wheat-160.webp" alt="" aria-hidden="true" width="180" height="180"><span>VERATUS</span></a>
        <address class="rodape__vendedor" aria-label="Identificação do vendedor"><b>Veratus</b><span>São Paulo/SP</span><a href="mailto:veratus.ltda@gmail.com">veratus.ltda@gmail.com</a><a class="purchase-link" href="{general}" target="_blank" rel="noopener">(11) 95832-3612</a><!--seller-document--></address>
        <nav aria-label="Links do rodapé"><a href="https://www.instagram.com/veratus_oficial/" target="_blank" rel="noopener">Instagram</a><a href="privacy.html">Privacidade</a><a href="condicoes-de-compra.html">Condições de compra</a></nav>
        <small>© <span data-year>2026</span> Veratus</small>
      </div>
    </div>
  </footer>

  <div class="barra-movel" data-mobile-bar>
    <p>{bar_price}</p>
    <a class="botao botao--ouro purchase-link" href="{general}" target="_blank" rel="noopener">Conversar</a>
  </div>

  <dialog class="vista" id="vista" aria-labelledby="vista-nome">
    <div class="vista__topo">
      <button class="vista__voltar" type="button" data-view-close>Voltar à coleção</button>
      <p class="vista__posicao" data-view-position></p>
    </div>
    <figure class="palco" data-stage>
      <div class="palco__ambiente" aria-hidden="true"></div>
      <div class="palco__cena">
        <div class="palco__chao" aria-hidden="true">
          <svg class="palco__mostrador" viewBox="0 0 200 200"><circle cx="100" cy="100" r="97"/>{dial}<circle class="pip" cx="100" cy="23" r="3.2"/></svg>
        </div>
        <div class="palco__entrada">
          <div class="palco__giro">
            <span class="palco__borda palco__borda--esquerda" aria-hidden="true"></span>
            <span class="palco__borda palco__borda--direita" aria-hidden="true"></span>
            <div class="palco__painel">
              <img class="palco__foto" src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7" alt="Relógio Veratus" width="1122" height="1402" data-view-image>
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
        <p class="vista__estilo" data-view-style></p>
        <h2 id="vista-nome" tabindex="-1" autofocus data-view-name>Relógio</h2>
        <p class="vista__descricao" data-view-description></p>
        <p class="vista__preco"><b data-view-price></b><span>Frete grátis e entrega em até 7 dias, sem taxas adicionais.</span></p>
        <a class="botao botao--ouro purchase-link" href="{general}" target="_blank" rel="noopener" data-view-order>Pedir pelo WhatsApp</a>
      </section>
      <section class="vista__bloco" aria-labelledby="vista-passos">
        <h3 id="vista-passos">Como o pedido funciona</h3>
        <ol class="vista__passos">
          <li><span class="vista__numero">1</span>O WhatsApp abre com o modelo e a referência da sua visita.</li>
          <li><span class="vista__numero">2</span>A equipe confirma disponibilidade, valor e forma de pagamento antes do pedido.</li>
          <li><span class="vista__numero">3</span>Você recebe em até 7 dias, com frete grátis.</li>
        </ol>
        <p class="vista__nota">Você pode desistir em até 7 dias após o recebimento. <a href="condicoes-de-compra.html">Condições de compra</a></p>
      </section>
      <section class="vista__bloco" aria-labelledby="vista-outros">
        <h3 id="vista-outros">Outros modelos</h3>
        <ul class="vista__outros" role="list" data-view-others></ul>
      </section>
    </div>
    <div class="vista__barra">
      <p><b data-view-price></b><span>Frete grátis</span></p>
      <a class="botao botao--ouro purchase-link" href="{general}" target="_blank" rel="noopener" data-view-order>Pedir pelo WhatsApp</a>
    </div>
  </dialog>

  <script src="site.js"></script>
</body>
</html>
"""


def main() -> None:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    OUTPUT.write_text(render(catalog), encoding="utf-8")
    print(f"landing/index.html montado a partir de {len(catalog)} itens do catálogo")


if __name__ == "__main__":
    main()
