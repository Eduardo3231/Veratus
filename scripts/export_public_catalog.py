"""Export the public catalogue projection from the Product Master.

The projection is written to landing/catalog.json and the storefront page is
built from it (scripts/build_landing.py), so a new photo or price reaches the
site in the same step. No commercial or supplier-only field is exported.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "landing" / "catalog.json"
sys.path.insert(0, str(ROOT))

from scripts.build_landing import OUTPUT as PAGE
from scripts.build_landing import render as render_page
from veratus_agents.catalog import load_catalog, public_catalog


def main() -> None:
    source = load_catalog()
    projection = public_catalog(source)
    # Render first: a catalogue the page cannot show leaves both files untouched.
    page = render_page(projection)
    OUTPUT.write_text(
        json.dumps(projection, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"Exported {len(projection)} public products from {len(source)} master records"
    )
    PAGE.write_text(page, encoding="utf-8")


if __name__ == "__main__":
    main()
