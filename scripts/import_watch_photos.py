"""Publish real photos of the watches on the storefront.

Put one photo per model in ``incoming/relogios/<id>.jpg`` (or .jpeg, .png,
.webp), for example ``incoming/relogios/ocean-blue.jpg``, and run:

    python scripts/import_watch_photos.py --sem-marca-de-terceiros

Each photo is cropped to 4:5, resized, saved as WebP without EXIF (no location
or camera data) in ``landing/assets/watches/`` and recorded in the Product
Master (``image``, ``image_status: REAL_PHOTO``, ``image_source``). The public
catalogue is exported again, so the card shows the photo instead of the
illustration. The flag records that the photos show the exact piece, with no
third-party name or logo.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "catalog" / "products.json"
OUTPUT_DIR = ROOT / "landing" / "assets" / "watches"
SIZE = (1122, 1402)
EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
sys.path.insert(0, str(ROOT))


def _crop_to_card(image: Image.Image) -> Image.Image:
    image = ImageOps.exif_transpose(image).convert("RGB")
    return ImageOps.fit(image, SIZE, method=Image.Resampling.LANCZOS)


def import_photos(
    source: Path, *, today: str | None = None, export: bool = True
) -> list[str]:
    products = json.loads(CATALOG.read_text(encoding="utf-8"))
    watches = {
        item["id"]: item
        for item in products
        if (item.get("collection") or "watches") == "watches"
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    imported = []
    for photo in sorted(source.iterdir()):
        if photo.suffix.lower() not in EXTENSIONS:
            continue
        product = watches.get(photo.stem.lower())
        if product is None:
            print(f"ignorada: {photo.name} (nome não é o id de um relógio)")
            continue
        target = OUTPUT_DIR / f"{product['id']}.webp"
        with Image.open(photo) as image:
            # Saving without exif= drops location and camera metadata.
            _crop_to_card(image).save(target, "WEBP", quality=82, method=6)
        product["image"] = f"assets/watches/{product['id']}.webp"
        product["image_status"] = "REAL_PHOTO"
        product["image_source"] = (
            f"foto do fundador, {today or datetime.now(ZoneInfo('America/Sao_Paulo')).date().isoformat()}"
        )
        product["alt"] = f"Relógio {product['name']} da Veratus"
        imported.append(product["id"])
        print(f"ok: {product['name']} -> {product['image']}")
    if imported:
        CATALOG.write_text(
            json.dumps(products, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        from scripts.export_public_catalog import main as export_public_catalog

        export_public_catalog()
    return imported


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pasta", nargs="?", default=str(ROOT / "incoming" / "relogios"))
    parser.add_argument(
        "--sem-marca-de-terceiros",
        action="store_true",
        help="confirma que as fotos mostram a peça exata, sem nome ou logo de outra marca",
    )
    args = parser.parse_args()
    if not args.sem_marca_de_terceiros:
        print(
            "Confirme com --sem-marca-de-terceiros que as fotos são da peça exata, sem marca de terceiros."
        )
        return 2
    source = Path(args.pasta)
    if not source.is_dir():
        print(f"Pasta não encontrada: {source}")
        return 1
    imported = import_photos(source)
    print(f"{len(imported)} foto(s) publicada(s) no catálogo.")
    return 0 if imported else 1


if __name__ == "__main__":
    raise SystemExit(main())
