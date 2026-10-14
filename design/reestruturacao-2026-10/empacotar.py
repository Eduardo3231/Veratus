"""Junta o protótipo num arquivo HTML só (CSS, JS e imagens embutidos) para ver no app.

Uso, na raiz do repositório:
    python3 -I design/reestruturacao-2026-10/empacotar.py design/reestruturacao-2026-10 . /tmp/previa.html
As fotos de catálogo e joias são reduzidas a 820 px de largura só nesta cópia.
"""

import base64
import io
import re
import sys
from pathlib import Path

from PIL import Image

pasta, repo, saida = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
SITE = "https://veratus.onrender.com/"
cache = {}


def data_uri(caminho_relativo):
    if caminho_relativo in cache:
        return cache[caminho_relativo]
    arquivo = repo / "landing" / "assets" / caminho_relativo
    imagem = Image.open(arquivo)
    if caminho_relativo.startswith(("catalog/", "feminine/")) and imagem.width > 820:
        imagem = imagem.resize(
            (820, round(imagem.height * 820 / imagem.width)), Image.LANCZOS
        )
        buffer = io.BytesIO()
        imagem.save(buffer, "WEBP", quality=80, method=6)
        dados = buffer.getvalue()
    else:
        dados = arquivo.read_bytes()
    uri = "data:image/webp;base64," + base64.b64encode(dados).decode()
    cache[caminho_relativo] = uri
    return uri


def embutir(texto):
    return re.sub(
        r"\.\./\.\./landing/assets/([\w./-]+\.webp)",
        lambda m: data_uri(m.group(1)),
        texto,
    )


html = (pasta / "index.html").read_text(encoding="utf-8")
css = embutir((pasta / "estilo.css").read_text(encoding="utf-8"))
js = (pasta / "vitrine.js").read_text(encoding="utf-8")
assert "</script" not in js and "</style" not in css

html = html.replace(
    '<link rel="stylesheet" href="estilo.css">', f"<style>\n{css}</style>"
)
html = html.replace('<script src="vitrine.js"></script>', f"<script>\n{js}</script>")
html = html.replace(
    "../../landing/condicoes-de-compra.html", SITE + "condicoes-de-compra.html"
)
html = html.replace("../../landing/privacy.html", SITE + "privacy.html")
html = embutir(html)
restante = re.findall(r"\.\./\.\./[\w./-]+", html)
assert not restante, restante

saida.write_text(html, encoding="utf-8")
print(f"{saida}: {len(html) // 1024} KB, {len(cache)} imagens embutidas")
