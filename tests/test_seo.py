"""Share image, robots.txt and sitemap.xml for https://veratus.onrender.com."""

from __future__ import annotations

import re
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

from integrations.webhook import app

LANDING = Path(__file__).resolve().parents[1] / "landing"
SITE = "https://veratus.onrender.com/"
SHARE_IMAGE = f"{SITE}assets/og/veratus-og-1200x630.jpg"


def _meta(html: str, attribute: str, name: str) -> str:
    match = re.search(rf'<meta {attribute}="{re.escape(name)}" content="([^"]*)"', html)
    assert match, name
    return match.group(1)


def _jpeg_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    offset = 2
    while offset < len(data):
        marker, length = (
            data[offset + 1],
            struct.unpack(">H", data[offset + 2 : offset + 4])[0],
        )
        if marker in (0xC0, 0xC1, 0xC2):  # start of frame: height, width
            height, width = struct.unpack(">HH", data[offset + 5 : offset + 9])
            return width, height
        offset += 2 + length
    raise AssertionError("JPEG sem cabeçalho de quadro")


def test_share_image_is_absolute_and_1200x630() -> None:
    html = (LANDING / "index.html").read_text(encoding="utf-8")

    assert _meta(html, "property", "og:image") == SHARE_IMAGE
    assert _meta(html, "name", "twitter:image") == SHARE_IMAGE
    assert _meta(html, "property", "og:image:width") == "1200"
    assert _meta(html, "property", "og:image:height") == "630"
    assert _jpeg_size(LANDING / SHARE_IMAGE.removeprefix(SITE)) == (1200, 630)


def test_robots_opens_the_site_and_closes_operations() -> None:
    lines = (LANDING / "robots.txt").read_text(encoding="utf-8").splitlines()

    assert "Allow: /" in lines
    for private in ("/os", "/agent", "/integrations"):
        assert f"Disallow: {private}" in lines
    assert f"Sitemap: {SITE}sitemap.xml" in lines


def test_sitemap_lists_only_existing_public_pages() -> None:
    namespace = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    root = ET.parse(LANDING / "sitemap.xml").getroot()
    urls = [item.text for item in root.findall("sm:url/sm:loc", namespace)]

    assert urls[0] == SITE
    for url in urls[1:]:
        assert url.startswith(SITE) and (LANDING / url.removeprefix(SITE)).exists()
    assert not any(part in url for url in urls for part in ("/os", "/agent"))


def test_flask_serves_robots_and_sitemap() -> None:
    client = app.test_client()

    robots = client.get("/robots.txt")
    sitemap = client.get("/sitemap.xml")

    assert robots.status_code == 200 and robots.mimetype == "text/plain"
    assert sitemap.status_code == 200 and "xml" in sitemap.mimetype
    assert b"Disallow: /os" in robots.data
