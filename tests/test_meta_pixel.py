"""Meta Pixel 1633870688525258 on every public page, disclosed in the privacy page."""

from __future__ import annotations

import re
from pathlib import Path

LANDING = Path(__file__).resolve().parents[1] / "landing"
PIXEL_ID = "1633870688525258"
PAGES = ("index.html", "condicoes-de-compra.html", "privacy.html")


def test_every_public_page_loads_the_pixel_once() -> None:
    for page in PAGES:
        html = (LANDING / page).read_text(encoding="utf-8")
        head, body = html.split("</head>", 1)

        assert head.count(f"fbq('init', '{PIXEL_ID}');") == 1, page
        assert "fbq('track', 'PageView');" in head, page
        assert "https://connect.facebook.net/en_US/fbevents.js" in head, page
        # Local and test visits are not counted.
        assert "localhost|127\\." in head, page
        noscript = re.search(r"<noscript>(.*?)</noscript>", body, re.DOTALL)
        assert noscript, page
        assert f"tr?id={PIXEL_ID}&amp;ev=PageView&amp;noscript=1" in noscript.group(1)
        assert 'alt="" aria-hidden="true"' in noscript.group(1)


def test_site_tracks_whatsapp_contacts_without_the_message() -> None:
    script = (LANDING / "site.js").read_text(encoding="utf-8")
    start = script.index("function trackPixel")
    pixel = script[start : script.index("function productImage")]

    assert "trackPixel('Contact', pixelProduct(link.dataset.productId))" in pixel
    assert "trackPixel('ViewContent', pixelProduct(product.id))" in script
    assert "typeof window.fbq === 'function'" in pixel
    # Only product id, name and public price: the WhatsApp text never goes to Meta.
    assert "createWhatsAppLink" not in pixel and "message" not in pixel


def test_privacy_page_discloses_the_pixel() -> None:
    privacy = (LANDING / "privacy.html").read_text(encoding="utf-8")

    assert "Pixel da Meta" in privacy
    assert "cookies" in privacy
    assert "https://www.facebook.com/privacy/policy/" in privacy
