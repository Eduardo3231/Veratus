"""Mídia com marca de terceiros fica fora do site público e da fila social."""

from __future__ import annotations

import json
import re
from pathlib import Path

from integrations.meta_content_publisher import validate_payload
from veratus_agents.catalog import public_catalog

ROOT = Path(__file__).resolve().parents[1]
LANDING = ROOT / "landing"
QUARANTINE = ROOT / "quarantine" / "third-party-marks"
PAGES = (
    "index.html",
    "site.js",
    "styles.css",
    "privacy.html",
    "condicoes-de-compra.html",
)
REMOVED_FOLDERS = (
    "assets/catalog/",
    "assets/video/",
    "assets/campaign/",
    "assets/products/",
    "social/",
)


def _manifest() -> list[str]:
    data = json.loads((QUARANTINE / "manifest.json").read_text(encoding="utf-8"))
    return data["original_paths"]


def _public_texts() -> dict[str, str]:
    texts = {page: (LANDING / page).read_text(encoding="utf-8") for page in PAGES}
    texts["catalog.json"] = (LANDING / "catalog.json").read_text(encoding="utf-8")
    return texts


def test_quarantined_media_never_returns_to_the_public_folder() -> None:
    paths = _manifest()

    assert len(paths) >= 40
    for path in paths:
        assert (QUARANTINE / path).exists(), path
        assert not (ROOT / path).exists(), path
    assert "quarantine" in (ROOT / ".dockerignore").read_text(encoding="utf-8")


def test_site_references_no_removed_media_and_no_video() -> None:
    for name, text in _public_texts().items():
        for folder in REMOVED_FOLDERS:
            assert folder not in text, (name, folder)
    assert "<video" not in _public_texts()["index.html"]


def test_every_asset_the_site_references_exists() -> None:
    for name, text in _public_texts().items():
        for asset in set(
            re.findall(r"assets/[A-Za-z0-9_./-]+\.(?:webp|png|jpe?g|mp4|svg)", text)
        ):
            assert (LANDING / asset).exists(), (name, asset)


def test_watches_wait_for_real_photos_and_jewelry_keeps_its_own() -> None:
    projection = public_catalog()
    watches = [item for item in projection if item["collection"] == "watches"]
    jewelry = [item for item in projection if item["collection"] == "feminine"]

    assert watches and all(item["image"] is None for item in watches)
    assert all(item["image_status"] == "NEEDS_REAL_PHOTO" for item in watches)
    for item in jewelry:
        for image in item.get("images") or [item.get("primary_image")]:
            assert (LANDING / image).exists(), image


def test_social_publisher_refuses_quarantined_media() -> None:
    blocked = {
        "type": "IMAGE",
        "caption": "Coleção Veratus",
        "media_url": "https://veratus.onrender.com/social/posts/navy-gold.jpg",
    }
    allowed = {**blocked, "media_url": "https://veratus.onrender.com/assets/og/x.jpg"}

    assert validate_payload(blocked, check_url=False) == [
        "mídia em quarentena: mostra marca de terceiros"
    ]
    assert validate_payload(allowed, check_url=False) == []
