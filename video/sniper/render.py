"""Renderiza o vídeo vertical do sniper (1080x1920, 30 fps) com o Chromium e o ffmpeg.

    python video/sniper/render.py --previa --quadros 1,4.5,5.85,10,14,16.5,18   # só PNGs
    python video/sniper/render.py --previa     # vídeo inteiro, com quadros marcados no lugar das fotos
    python video/sniper/render.py              # vídeo final (precisa das fotos)

A pasta de trabalho (padrão runtime/video/sniper, fora do Git) precisa de ``fontes/`` (Cormorant
Garamond e Jost, WOFF2, licença OFL) e, no vídeo final, de ``fotos/`` com as fotos do vídeo das
correntes. A abertura (atirador, alvo, mira, tiro e bala) é desenhada no ``cena.html``, sem foto
nem vídeo de terceiros. Textos e tempos vêm de ``roteiro.json``; o som, de ``audio.py``.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
ROOT = AQUI.parents[1]
EMBLEMA = ROOT / "landing" / "assets" / "veratus-v-wheat-alpha.png"


def preparar(trabalho: Path, previa: bool) -> tuple[Path, dict]:
    roteiro = json.loads((AQUI / "roteiro.json").read_text(encoding="utf-8"))
    roteiro["previa"] = previa
    for pasta in ("fontes",) if previa else ("fontes", "fotos"):
        if not (trabalho / pasta).is_dir():
            sys.exit(f"falta {trabalho / pasta}")
    # o emblema entra como data URL: o canvas lê os pixels dele para a areia formar o V
    emblema = "data:image/png;base64," + base64.b64encode(EMBLEMA.read_bytes()).decode()
    html = (AQUI / "cena.html").read_text(encoding="utf-8")
    html = html.replace("__BASE__", trabalho.resolve().as_uri())
    html = html.replace("__EMBLEMA__", emblema)
    html = html.replace("__ROTEIRO__", json.dumps(roteiro, ensure_ascii=False))
    pagina = trabalho / "cena.render.html"
    pagina.write_text(html, encoding="utf-8")
    return pagina, roteiro


def codificador(fps: int, saida: Path) -> subprocess.Popen:
    comando = [
        "ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(fps), "-c:v", "png", "-i", "-",
        "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-pix_fmt", "yuv420p", "-g", str(fps),
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-r", str(fps), str(saida),
    ]  # fmt: skip
    return subprocess.Popen(comando, stdin=subprocess.PIPE)


def juntar(trabalho: Path, roteiro: dict, n: int, saida: Path, com_audio: bool) -> None:
    lista = trabalho / "partes.txt"
    lista.write_text(
        "".join(f"file '{trabalho / f'parte-{i}.mp4'}'\n" for i in range(n)),
        encoding="utf-8",
    )
    comando = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0"]
    comando += ["-i", str(lista)]
    if com_audio:
        sys.path.insert(0, str(AQUI))
        from audio import sintetizar

        audio = sintetizar(roteiro, trabalho / "audio.wav")
        # limitador com sobreamostragem: o pico real do tiro fica abaixo de 0 dBFS no AAC
        limite = "aresample=192000,alimiter=limit=0.7:level=0,aresample=48000"
        comando += ["-i", str(audio), "-af", limite]
        comando += ["-c:a", "aac", "-b:a", "192k", "-shortest"]
    comando += ["-c:v", "copy", "-movflags", "+faststart", str(saida)]
    subprocess.run(comando, check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--trabalho", type=Path, default=ROOT / "runtime/video/sniper")
    parser.add_argument("--saida", type=Path)
    parser.add_argument("--quadros", help="tempos em segundos, separados por vírgula")
    parser.add_argument("--previa", action="store_true", help="sem as fotos do produto")
    parser.add_argument("--sem-audio", action="store_true")
    parser.add_argument(
        "--processos", type=int, default=4, help="trechos renderizados em paralelo"
    )
    parser.add_argument("--parte", help="interno: <índice>/<total>")
    args = parser.parse_args(argv)

    trabalho = args.trabalho.resolve()
    pagina, roteiro = preparar(trabalho, args.previa)
    largura, altura, fps = roteiro["largura"], roteiro["altura"], roteiro["fps"]
    total = round(roteiro["duracao"] * fps)

    if not args.quadros and not args.parte:
        n = max(1, args.processos)
        base = [sys.executable, __file__, "--trabalho", str(trabalho)]
        base += ["--previa"] if args.previa else []
        filhos = [subprocess.Popen([*base, "--parte", f"{i}/{n}"]) for i in range(n)]
        codigos = [filho.wait() for filho in filhos]  # espera todos, mesmo se um falhar
        if any(codigos):
            sys.exit("falha em um dos trechos")
        nome = "veratus-sniper-previa.mp4" if args.previa else "veratus-sniper.mp4"
        saida = (args.saida or trabalho / nome).resolve()
        juntar(trabalho, roteiro, n, saida, not args.sem_audio)
        print(saida)
        return 0

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        # CHROMIUM_EXECUTABLE: quando o Playwright do Python não traz o próprio navegador
        navegador = p.chromium.launch(
            executable_path=os.environ.get("CHROMIUM_EXECUTABLE") or None,
            args=["--force-color-profile=srgb"],
        )
        aba = navegador.new_page(viewport={"width": largura, "height": altura})
        aba.goto(pagina.as_uri())
        aba.wait_for_function("window.pronto === true || !!window.erro", timeout=90000)
        if erro := aba.evaluate("window.erro"):
            sys.exit(erro)

        def quadro(t: float) -> bytes:
            aba.evaluate("(t) => window.draw(t)", t)
            return aba.screenshot(type="png")

        if args.quadros:
            for texto in args.quadros.split(","):
                destino = trabalho / f"quadro-{float(texto):05.2f}.png"
                destino.write_bytes(quadro(float(texto)))
                print(destino)
            navegador.close()
            return 0

        indice, n = (int(x) for x in args.parte.split("/"))
        inicio, fim = total * indice // n, total * (indice + 1) // n
        ffmpeg = codificador(fps, trabalho / f"parte-{indice}.mp4")
        for i in range(inicio, fim):
            ffmpeg.stdin.write(quadro(i / fps))
            if (i - inicio) % 90 == 0:
                print(f"parte {indice}: quadro {i - inicio}/{fim - inicio}", flush=True)
        ffmpeg.stdin.close()
        ffmpeg.wait()
        navegador.close()
        return ffmpeg.returncode


if __name__ == "__main__":
    raise SystemExit(main())
