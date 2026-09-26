"""Static guards for the landing: content must not depend on JS or scrolling."""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path

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
