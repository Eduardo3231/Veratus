"""Gera os criativos estáticos das correntes (feed 4:5 e story 9:16) com o Chromium.

    python criativos/correntes/render.py

A pasta de trabalho (padrão runtime/criativos/correntes, fora do Git) precisa ter ``fotos/``,
``fontes/``, ``emblema-circular.png`` (o círculo recortado de landing/assets/veratus-emblem.jpeg)
e ``emblema.png``. Cada ``<section class="peca">`` de ``modelos.html`` vira um PNG.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
ROOT = AQUI.parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--trabalho", type=Path, default=ROOT / "runtime/criativos/correntes"
    )
    args = parser.parse_args(argv)
    trabalho = args.trabalho.resolve()
    for item in ("fotos", "fontes", "emblema-circular.png"):
        if not (trabalho / item).exists():
            sys.exit(f"falta {trabalho / item}")
    html = (AQUI / "modelos.html").read_text(encoding="utf-8")
    pagina = trabalho / "modelos.render.html"
    pagina.write_text(html.replace("__BASE__", trabalho.as_uri()), encoding="utf-8")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        navegador = p.chromium.launch(args=["--force-color-profile=srgb"])
        aba = navegador.new_page(viewport={"width": 1200, "height": 2000})
        aba.goto(pagina.as_uri())
        aba.wait_for_function("window.pronto === true", timeout=60000)
        if erro := aba.evaluate("window.erro"):
            sys.exit(erro)
        for peca in aba.locator("section.peca").all():
            nome = peca.get_attribute("id")
            destino = trabalho / f"veratus-{nome}.png"
            peca.screenshot(path=str(destino))
            print(destino)
        navegador.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
