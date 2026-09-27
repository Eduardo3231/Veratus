"""Real watch photos replace the illustration without leaking EXIF data."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from PIL import Image

from scripts import import_watch_photos as importer

ROOT = Path(__file__).resolve().parents[1]


def test_photo_is_cropped_stripped_and_recorded(tmp_path: Path, monkeypatch) -> None:
    catalog = tmp_path / "products.json"
    shutil.copy(ROOT / "catalog" / "products.json", catalog)
    monkeypatch.setattr(importer, "CATALOG", catalog)
    monkeypatch.setattr(importer, "OUTPUT_DIR", tmp_path / "watches")
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    exif = Image.Exif()
    exif[0x010F] = "Camera"  # Make
    Image.new("RGB", (3000, 2000), "#123763").save(
        incoming / "ocean-blue.jpg", exif=exif
    )
    Image.new("RGB", (400, 400)).save(incoming / "nao-existe.jpg")

    imported = importer.import_photos(incoming, today="2026-09-27", export=False)

    assert imported == ["ocean-blue"]
    with Image.open(tmp_path / "watches" / "ocean-blue.webp") as photo:
        assert photo.size == importer.SIZE
        assert not photo.getexif()
    product = next(
        p for p in json.loads(catalog.read_text("utf-8")) if p["id"] == "ocean-blue"
    )
    assert product["image"] == "assets/watches/ocean-blue.webp"
    assert product["image_status"] == "REAL_PHOTO"
    assert product["image_source"] == "foto do fundador, 2026-09-27"


def test_import_requires_the_no_third_party_mark_confirmation(
    monkeypatch, capsys
) -> None:
    monkeypatch.setattr("sys.argv", ["import_watch_photos.py"])

    assert importer.main() == 2
    assert "--sem-marca-de-terceiros" in capsys.readouterr().out
