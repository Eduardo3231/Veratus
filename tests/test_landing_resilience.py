"""Static guards for the landing: content must not depend on JS or scrolling."""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote

LANDING = Path(__file__).resolve().parents[1] / "landing"
WHATSAPP = "https://wa.me/5511958323612"
HIDDEN_OPACITY = re.compile(r"opacity:\s*0(?![.\d])")


class _LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict[str, str]] = []
        self._catalog_depth = 0
        self.catalog_links: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        if self._catalog_depth:
            self._catalog_depth += 1
        elif values.get("id") == "relogios":
            self._catalog_depth = 1
        if tag == "a" and "purchase-link" in values.get("class", "").split():
            self.links.append(values)
            if self._catalog_depth:
                self.catalog_links.append(values)

    def handle_endtag(self, tag: str) -> None:
        if self._catalog_depth:
            self._catalog_depth -= 1


def _css() -> str:
    return (LANDING / "styles.css").read_text(encoding="utf-8")


def _html_links() -> _LinkCollector:
    parser = _LinkCollector()
    parser.feed((LANDING / "index.html").read_text(encoding="utf-8"))
    return parser


def test_only_the_watch_view_entrance_starts_invisible() -> None:
    """Page content never waits for an animation; only the view a click opens fades in."""
    keyframes = dict(
        re.findall(r"@keyframes ([\w-]+) \{(.*?)\}\s*\}", _css(), re.DOTALL)
    )
    hiding = {name for name, body in keyframes.items() if HIDDEN_OPACITY.search(body)}

    assert hiding == {"palco-entrada"}
    # "backwards": once the entrance ends, nothing stays applied.
    assert "palco-entrada 1.1s cubic-bezier(.2, .75, .2, 1) backwards" in _css()


def test_page_opens_without_an_intro_gate() -> None:
    html = (LANDING / "index.html").read_text(encoding="utf-8")
    assert "intro-gate" not in html and "intro-gate" not in _css()


def test_page_is_built_from_the_public_catalog() -> None:
    from scripts.build_landing import render

    catalog = json.loads((LANDING / "catalog.json").read_text(encoding="utf-8"))
    html = (LANDING / "index.html").read_text(encoding="utf-8")
    assert html == render(catalog), "rode python scripts/build_landing.py"


def test_watch_orders_work_before_the_script_runs() -> None:
    links = _html_links()
    watches = [
        item
        for item in json.loads((LANDING / "catalog.json").read_text(encoding="utf-8"))
        if item["collection"] == "watches"
    ]
    orders = [link for link in links.catalog_links if link.get("data-order") == "1"]
    assert {link["data-product-id"] for link in orders} == {w["id"] for w in watches}
    assert all(link["href"].startswith(WHATSAPP) for link in links.catalog_links)


def test_purchase_links_use_official_number_and_known_products() -> None:
    catalog_ids = {
        item["id"]
        for item in json.loads((LANDING / "catalog.json").read_text(encoding="utf-8"))
    }
    links = _html_links().links
    assert links
    for link in links:
        assert link["href"].startswith(WHATSAPP)
        assert link.get("target") != "_blank" or "noopener" in link.get("rel", "")
        if "data-product-id" in link:
            assert link["data-product-id"] in catalog_ids
            assert link.get("data-product-name")


def _visit_reference(source: str = "site-direto") -> str:
    """Python mirror of campaignReference() in site.js (FNV-1a, base 36)."""
    value = 2166136261
    for character in source:
        value = ((value ^ ord(character)) * 16777619) & 0xFFFFFFFF
    digits, reference = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ", ""
    while value:
        value, remainder = divmod(value, 36)
        reference = digits[remainder] + reference
    return f"VT-{reference}"


def _default_whatsapp_link(
    product_name: str = "", product_id: str = "", order: bool = False
) -> str:
    """What createWhatsAppLink() in site.js returns for a visit without UTMs."""
    opening = (
        f"Olá! Vim pelo site da Veratus e tenho interesse em {product_name}."
        if product_name
        else "Olá! Vim pelo site da Veratus e quero conhecer as coleções."
    )
    reference = f"Referência da visita: {_visit_reference()}"
    if product_id:
        reference += f" | produto={product_id}"
    intent = (
        "Quero fazer o pedido e confirmar a disponibilidade."
        if order
        else "Gostaria de confirmar disponibilidade e valor."
    )
    message = f"{opening}\n{intent}\n{reference}"
    # Characters encodeURIComponent() leaves as they are.
    unreserved = "-_.!~*'()"
    return f"{WHATSAPP}?text={quote(message, safe=unreserved)}"


def test_every_static_whatsapp_link_carries_the_visit_reference() -> None:
    anchors = re.compile(r'<a\b[^>]*href="https://wa\.me/[^>]*>')
    for page in ("index.html", "condicoes-de-compra.html", "privacy.html"):
        found = anchors.findall((LANDING / page).read_text(encoding="utf-8"))
        assert found, page
        for tag in found:
            href = re.search(r'href="([^"]+)"', tag).group(1)
            name = re.search(r'data-product-name="([^"]*)"', tag)
            product = re.search(r'data-product-id="([^"]*)"', tag)
            expected = _default_whatsapp_link(
                name.group(1) if name else "",
                product.group(1) if product else "",
                order='data-order="1"' in tag,
            )
            assert href == expected, (page, tag)


def test_site_js_builds_the_same_default_message() -> None:
    script = (LANDING / "site.js").read_text(encoding="utf-8")

    assert "'Gostaria de confirmar disponibilidade e valor.'" in script
    assert "'Quero fazer o pedido e confirmar a disponibilidade.'" in script
    assert "'site-direto'" in script
    assert "hash = Math.imul(hash, 16777619)" in script
    # The watch view CTA is rewritten too, so no WhatsApp link lacks a reference.
    assert "document.querySelectorAll('.purchase-link').forEach" in script


def test_hero_still_and_brand_mark_stay_light() -> None:
    html = (LANDING / "index.html").read_text(encoding="utf-8")
    css = (LANDING / "styles.css").read_text(encoding="utf-8")

    assert '<div class="hero-still" aria-hidden="true"></div>' in html
    assert "hero-video-control" not in html
    assert "assets/hero/veratus-hero-alpes-720.webp" in css
    assert (
        LANDING / "assets/hero/veratus-hero-alpes-1280.webp"
    ).stat().st_size < 80_000
    assert (LANDING / "assets/hero/veratus-hero-alpes-720.webp").stat().st_size < 40_000
    assert "veratus-v-wheat-alpha.png" not in html
    for size in (160, 720):
        mark = LANDING / f"assets/veratus-v-wheat-{size}.webp"
        assert mark.stat().st_size < 100_000


def test_every_image_has_alternative_text() -> None:
    for page in ("index.html", "condicoes-de-compra.html", "privacy.html"):
        html = (LANDING / page).read_text(encoding="utf-8")
        for tag in re.findall(r"<img\b[^>]*>", html):
            alt = re.search(r'alt="([^"]*)"', tag)
            assert alt, (page, tag)
            # Only purely decorative layers, hidden from assistive tech, may be empty.
            assert alt.group(1).strip() or 'aria-hidden="true"' in tag, (page, tag)


def test_watch_stage_only_turns_and_stops_for_reduced_motion() -> None:
    html = (LANDING / "index.html").read_text(encoding="utf-8")
    css = _css()
    script = (LANDING / "site.js").read_text(encoding="utf-8")
    reduced = "".join(css.split("@media (prefers-reduced-motion: reduce)")[1:])

    # The 3D logo chapter left the site (founder, 14/10); the watch turns instead.
    assert "brand-3d" not in html and "inspection" not in html
    assert "data-stage" in html
    assert ".palco__giro { transform: rotateY(var(--giro, 0deg))" in css
    assert ".palco.is-entrando .palco__entrada { animation: none; }" in reduced
    assert "if (reducedMotion) return;" in script


UNVERIFIED_MATERIAL = re.compile(
    r"\b(ouro|prata|banhad[oa]s?|banho|zirc[oô]nias?|hipoalerg[eê]nic[oa]s?|"
    r"18k|925|a[cç]o cir[uú]rgico|folhead[oa]s?|cristal)\b",
    re.IGNORECASE,
)


def test_jewelry_chapter_mirrors_the_product_master() -> None:
    html = (LANDING / "index.html").read_text(encoding="utf-8")
    section = html.split('id="joias"', 1)[1].split('id="como-comprar"', 1)[0]
    catalog = {
        item["id"]: item
        for item in json.loads((LANDING / "catalog.json").read_text(encoding="utf-8"))
    }
    published = {
        product_id
        for product_id, item in catalog.items()
        if item["collection"] == "feminine" and item.get("site_visibility") == "PREVIEW"
    }
    ids = re.findall(r'<li class="joia" data-item data-product-id="([a-z-]+)"', section)

    # Noir Clover waits for its resemblance alert (docs/HANDOFF.md).
    assert set(ids) == published - {"noir-clover"} and "noir-clover" not in section
    for product_id in ids:
        item = catalog[product_id]
        assert f'src="{item["primary_image"]}" alt="{item["alt"]}"' in section
        assert f"<h4>{item['name']}</h4>" in section
        assert f"produto%3D{product_id}" in section
    assert section.count("Refer%C3%AAncia%20da%20visita%3A%20VT-") == len(ids)
    assert not UNVERIFIED_MATERIAL.search(re.sub(r"<[^>]+>", " ", section))
    assert "R$" not in section
