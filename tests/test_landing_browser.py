"""Browser regression for the landing ("header visible, content empty").

Runs only where Playwright and a Chromium-based browser are available; it is
skipped otherwise. Set VERATUS_LANDING_URL to check a deployed URL instead of
the local ``landing/`` directory.
"""

from __future__ import annotations

import functools
import os
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

LANDING = Path(__file__).resolve().parents[1] / "landing"
WHATSAPP = "https://wa.me/5511958323612?text="
VISIT_REFERENCE = "Refer%C3%AAncia%20da%20visita%3A%20VT-"
VIEWPORTS = {
    "desktop": {"width": 1440, "height": 900},
    "mobile": {"width": 390, "height": 844},
}
# Content must be visible without scrolling: this is what full-page captures,
# ad reviewers and slow devices see before any scroll timeline progresses.
VISIBLE_JS = """(selector) => {
  const element = document.querySelector(selector);
  if (!element) return {ok: false, reason: 'missing'};
  const box = element.getBoundingClientRect();
  let opacity = 1;
  for (let node = element; node; node = node.parentElement) {
    const style = getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden') {
      return {ok: false, reason: 'hidden'};
    }
    opacity *= Number(style.opacity);
  }
  return {ok: box.width > 0 && box.height > 0 && opacity > 0.99, opacity,
          width: box.width, height: box.height};
}"""
INTRO_GONE_JS = """() => {
  const gate = document.querySelector('#intro-gate');
  return Boolean(gate) && getComputedStyle(gate).visibility === 'hidden';
}"""
EXTERNAL_URL = os.getenv("VERATUS_LANDING_URL", "").strip()


class _QuietHandler(SimpleHTTPRequestHandler):
    js_delay_seconds = 0.0

    def do_GET(self) -> None:
        if self.js_delay_seconds and self.path.split("?")[0].endswith("/site.js"):
            time.sleep(self.js_delay_seconds)
        super().do_GET()

    def log_message(self, format: str, *args: object) -> None:
        return


def _serve_landing(js_delay_seconds: float = 0.0):
    handler_class = type(
        "LandingHandler", (_QuietHandler,), {"js_delay_seconds": js_delay_seconds}
    )
    handler = functools.partial(handler_class, directory=str(LANDING))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture(scope="module")
def landing_url():
    if EXTERNAL_URL:
        yield EXTERNAL_URL.rstrip("/") + "/"
        return
    server = _serve_landing()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()
    server.server_close()


@pytest.fixture
def delayed_landing_url():
    if EXTERNAL_URL:
        pytest.skip("atraso de JS só é simulado no servidor local")
    server = _serve_landing(js_delay_seconds=6.0)
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as playwright:
        launched = None
        for options in ({}, {"channel": "chrome"}, {"channel": "msedge"}):
            try:
                launched = playwright.chromium.launch(**options)
                break
            except sync_api.Error:
                continue
        if launched is None:
            pytest.skip("nenhum navegador Chromium disponível para o Playwright")
        yield launched
        launched.close()


def _assert_visible(page, selector: str) -> None:
    result = page.evaluate(VISIBLE_JS, selector)
    assert result["ok"], (selector, result)


def _open(browser, url: str, viewport: str, motion: str = "no-preference"):
    context = browser.new_context(viewport=VIEWPORTS[viewport], reduced_motion=motion)
    return context, context.new_page()


@pytest.mark.parametrize("motion", ["no-preference", "reduce"])
@pytest.mark.parametrize("viewport", sorted(VIEWPORTS))
def test_content_and_whatsapp_links_are_visible(
    browser, landing_url: str, viewport: str, motion: str
) -> None:
    context, page = _open(browser, landing_url, viewport, motion)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(landing_url, wait_until="domcontentloaded")
        page.wait_for_selector("#catalog-rail .product-card", timeout=15_000)
        page.wait_for_function(INTRO_GONE_JS, timeout=6_000)
        for selector in (
            "#hero-title",
            "#manifesto-title",
            "#catalog-title",
            "#catalog-rail .product-card",
            "#trust-title",
            "#contato .purchase-link",
        ):
            _assert_visible(page, selector)
        assert page.locator("#catalog-rail .product-card").count() >= 8

        hrefs = page.eval_on_selector_all(
            'a[href*="wa.me"]', "links => links.map(a => a.href)"
        )
        assert hrefs and all(href.startswith(WHATSAPP) for href in hrefs)
        assert all(VISIT_REFERENCE in href for href in hrefs)

        first = page.locator("#catalog-rail .product-card").first
        product_id = first.get_attribute("data-product-id")
        first.locator(".product-open").click()
        cta = page.get_attribute("#dialog-cta", "href")
        assert cta.startswith(WHATSAPP)
        assert f"produto%3D{product_id}" in cta and "VT-" in cta
        assert errors == []
    finally:
        context.close()


HIDDEN_LINKS_JS = """() => [...document.querySelectorAll('a[href*="wa.me"]')].map(a => {
  let hidden = false;
  for (let node = a; node; node = node.parentElement) {
    const style = getComputedStyle(node);
    if (style.display === 'none') return {rendered: false};
    if (style.visibility === 'hidden' || Number(style.opacity) < 0.05) hidden = true;
  }
  return {rendered: true, hidden, inert: Boolean(a.closest('[inert]')),
          text: a.textContent.trim()};
})"""


def test_hidden_whatsapp_links_leave_the_tab_order(browser, landing_url: str) -> None:
    context, page = _open(browser, landing_url, "mobile")
    try:
        page.goto(landing_url, wait_until="domcontentloaded")
        page.wait_for_selector("#catalog-rail .product-card", timeout=15_000)
        page.wait_for_function(INTRO_GONE_JS, timeout=6_000)
        links = page.evaluate(HIDDEN_LINKS_JS)
        # The closed menu and the not-yet-shown sticky CTA are invisible links.
        invisible = [item for item in links if item["rendered"] and item["hidden"]]
        assert len(invisible) >= 2
        assert all(item["inert"] for item in invisible), invisible

        page.click(".menu-toggle")
        assert page.evaluate("document.querySelector('.main-nav').inert") is False
    finally:
        context.close()


REMOVED_MEDIA = (
    "/assets/catalog/",
    "/assets/video/",
    "/assets/campaign/",
    "/assets/products/",
    "/social/",
)


@pytest.mark.parametrize("viewport", sorted(VIEWPORTS))
def test_page_loads_no_third_party_media(
    browser, landing_url: str, viewport: str
) -> None:
    context, page = _open(browser, landing_url, viewport)
    requested: list[str] = []
    page.on("request", lambda request: requested.append(request.url))
    try:
        page.goto(landing_url, wait_until="networkidle")
        page.wait_for_selector("#catalog-rail .product-card", timeout=15_000)
        page.mouse.wheel(0, 20_000)
        page.wait_for_timeout(800)
        pending = page.locator("#catalog-rail .product-visual--pending")

        assert page.locator("video").count() == 0
        assert pending.count() >= 8
        assert "Foto oficial em produção" in pending.first.text_content()
        assert not [url for url in requested if any(p in url for p in REMOVED_MEDIA)]
    finally:
        context.close()


@pytest.mark.parametrize("viewport", sorted(VIEWPORTS))
def test_content_survives_blocked_javascript(
    browser, landing_url: str, viewport: str
) -> None:
    context, page = _open(browser, landing_url, viewport)
    try:
        page.route("**/site.js", lambda route: route.abort())
        page.goto(landing_url, wait_until="domcontentloaded")
        page.wait_for_function(INTRO_GONE_JS, timeout=6_000)
        for selector in (
            "#hero-title",
            "#manifesto-title",
            "#catalog-title",
            "#catalog-rail .purchase-link",
            "#contato .purchase-link",
        ):
            _assert_visible(page, selector)
        fallback = page.get_attribute("#catalog-rail .purchase-link", "href")
        assert fallback.startswith(WHATSAPP) and VISIT_REFERENCE in fallback
        static_hrefs = page.eval_on_selector_all(
            'a[href*="wa.me"]', "links => links.map(a => a.href)"
        )
        assert all(VISIT_REFERENCE in href for href in static_hrefs)
    finally:
        context.close()


def test_content_is_visible_while_javascript_is_delayed(
    browser, delayed_landing_url: str
) -> None:
    context, page = _open(browser, delayed_landing_url, "desktop")
    try:
        page.goto(delayed_landing_url, wait_until="commit")
        page.wait_for_function(INTRO_GONE_JS, timeout=5_500)
        assert page.locator("#catalog-rail .product-card").count() == 0
        for selector in (
            "#hero-title",
            "#manifesto-title",
            "#catalog-rail .purchase-link",
            "#contato .purchase-link",
        ):
            _assert_visible(page, selector)
        page.wait_for_selector("#catalog-rail .product-card", timeout=15_000)
    finally:
        context.close()
