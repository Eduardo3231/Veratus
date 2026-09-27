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
        elif values.get("id") == "catalog-rail":
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


def _css_rules() -> list[tuple[str, str]]:
    return re.findall(r"([^{}]+)\{([^{}]*)\}", _css())


def _keyframes(name: str) -> str:
    match = re.search(rf"@keyframes {name} \{{(.*?)\}}\s*\}}", _css(), re.DOTALL)
    assert match, f"@keyframes {name} não encontrado"
    return match.group(1)


def _html_links() -> _LinkCollector:
    parser = _LinkCollector()
    parser.feed((LANDING / "index.html").read_text(encoding="utf-8"))
    return parser


def test_scroll_reveal_never_starts_invisible() -> None:
    assert not HIDDEN_OPACITY.search(_keyframes("reveal-by-view"))
    for selector, body in _css_rules():
        if "scroll-reveal" in selector:
            assert not HIDDEN_OPACITY.search(body), (selector.strip(), body)


def test_intro_gate_leaves_without_javascript() -> None:
    assert any(
        "intro-failsafe" in body
        for selector, body in _css_rules()
        if selector.strip() == ".intro-gate"
    )
    assert "visibility: hidden" in _keyframes("intro-failsafe")


def test_catalog_keeps_whatsapp_fallback_before_hydration() -> None:
    links = _html_links()
    assert links.catalog_links, "#catalog-rail precisa de CTA estático"
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


def _default_whatsapp_link(product_name: str = "", product_id: str = "") -> str:
    """What createWhatsAppLink() in site.js returns for a visit without UTMs."""
    opening = (
        f"Olá! Vim pelo site da Veratus e tenho interesse em {product_name}."
        if product_name
        else "Olá! Vim pelo site da Veratus e quero conhecer as coleções."
    )
    reference = f"Referência da visita: {_visit_reference()}"
    if product_id:
        reference += f" | produto={product_id}"
    message = f"{opening}\nGostaria de confirmar disponibilidade e valor.\n{reference}"
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
                name.group(1) if name else "", product.group(1) if product else ""
            )
            assert href == expected, (page, tag)


def test_site_js_builds_the_same_default_message() -> None:
    script = (LANDING / "site.js").read_text(encoding="utf-8")

    assert "'Gostaria de confirmar disponibilidade e valor.'" in script
    assert "'site-direto'" in script
    assert "hash = Math.imul(hash, 16777619)" in script
    # The dialog CTA is rewritten too, so no WhatsApp link lacks a reference.
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
            assert alt and alt.group(1).strip(), (page, tag)
