"""Build the optimized Veratus Feminino asset set from founder-supplied files.

This script is deterministic and intentionally selects one image for each product.
Alternative angles are documented in the import report instead of being copied to
the public catalogue.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

SOURCE_DIR = Path.home() / "Downloads"
TARGET_DIR = Path(__file__).resolve().parents[1] / "landing" / "assets" / "feminine"

ASSETS = {
    "essenza-anklet.webp": "ChatGPT Image 19 de set. de 2026, 17_19_37 (1).png",
    "verde-aura-necklace.webp": "ChatGPT Image 19 de set. de 2026, 17_19_37 (2).png",
    "noir-clover-necklace.webp": "ChatGPT Image 19 de set. de 2026, 17_19_38 (3).png",
    "rosa-aurea-necklace.webp": "ChatGPT Image 19 de set. de 2026, 17_19_38 (4).png",
    "lumiere-bracelet.webp": "ChatGPT Image 19 de set. de 2026, 17_19_38 (5).png",
    "celeste-link-bracelet.webp": "ChatGPT Image 19 de set. de 2026, 17_19_38 (7).png",
    "eclat-duo-necklace.webp": "ChatGPT Image 19 de set. de 2026, 17_19_51.png",
    "halo-verde-necklace.webp": "ChatGPT Image 19 de set. de 2026, 17_19_56.png",
    "celeste-anklet.webp": "ChatGPT Image 19 de set. de 2026, 17_20_03.png",
}

ALTERNATES_NOT_IMPORTED = {
    "ChatGPT Image 19 de set. de 2026, 17_19_38 (6).png": "celeste-link-bracelet.webp",
    "ChatGPT Image 19 de set. de 2026, 17_19_38 (8).png": "essenza-anklet.webp",
}


def main() -> None:
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    missing = [name for name in ASSETS.values() if not (SOURCE_DIR / name).exists()]
    if missing:
        raise FileNotFoundError(f"Founder assets missing: {missing}")

    for output_name, source_name in ASSETS.items():
        with Image.open(SOURCE_DIR / source_name) as image:
            rgb = image.convert("RGB")
            rgb.save(TARGET_DIR / output_name, "WEBP", quality=87, method=6)

    print(f"Imported {len(ASSETS)} unique assets into {TARGET_DIR}")
    print(f"Skipped {len(ALTERNATES_NOT_IMPORTED)} alternate angles")


if __name__ == "__main__":
    main()
