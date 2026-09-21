"""Export the public catalogue projection from the Product Master.

The generated file is a static fallback for the landing page. The frontend tries
the catalogue API first and uses this projection when it is hosted as a static
site. No commercial or supplier-only field is exported.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "landing" / "catalog.json"
sys.path.insert(0, str(ROOT))

from veratus_agents.catalog import load_catalog, public_catalog


def main() -> None:
    source = load_catalog()
    projection = public_catalog(source)
    OUTPUT.write_text(
        json.dumps(projection, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"Exported {len(projection)} public products from {len(source)} master records"
    )


if __name__ == "__main__":
    main()
