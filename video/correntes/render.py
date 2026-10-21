"""Renderiza o vídeo vertical das correntes (1080x1920, 30 fps) com o Chromium e o ffmpeg.

    python video/correntes/render.py --quadros 0.5,2.5,4.9,6      # só PNGs para conferir
    python video/correntes/render.py                              # vídeo completo

A pasta de trabalho (padrão runtime/video/correntes, fora do Git) precisa ter ``fotos/``
(corrente-busto-frente, corrente-busto-angulo, corrente-plana, crucifixo e rosto, em PNG) e
``fontes/`` (Cormorant Garamond e Jost, WOFF2, licença OFL). Os textos e os tempos vêm de
``roteiro.json``; o som é sintetizado por ``audio.py``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
ROOT = AQUI.parents[1]
EMBLEMA = ROOT / "landing" / "assets" / "veratus-v-wheat-alpha.png"


def preparar(trabalho: Path) -> tuple[Path, dict]:
    roteiro = json.loads((AQUI / "roteiro.json").read_text(encoding="utf-8"))
    for pasta in ("fotos", "fontes"):
        if not (trabalho / pasta).is_dir():
            sys.exit(f"falta {trabalho / pasta}")
    shutil.copy(EMBLEMA, trabalho / "emblema.png")
    html = (AQUI / "cena.html").read_text(encoding="utf-8")
    html = html.replace("__BASE__", trabalho.resolve().as_uri())
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
    comando = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(lista),
    ]
    if com_audio:
        sys.path.insert(0, str(AQUI))
        from audio import sintetizar

        audio = sintetizar(roteiro, trabalho / "audio.wav")
        comando += ["-i", str(audio), "-af", "loudnorm=I=-15:TP=-1.5:LRA=11"]
        comando += ["-c:a", "aac", "-b:a", "192k", "-shortest"]
    comando += ["-c:v", "copy", "-movflags", "+faststart", str(saida)]
    subprocess.run(comando, check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--trabalho", type=Path, default=ROOT / "runtime/video/correntes"
    )
    parser.add_argument("--saida", type=Path)
    parser.add_argument("--quadros", help="tempos em segundos, separados por vírgula")
    parser.add_argument("--sem-audio", action="store_true")
    parser.add_argument(
        "--processos", type=int, default=3, help="trechos renderizados em paralelo"
    )
    parser.add_argument("--parte", help="interno: <índice>/<total>")
    args = parser.parse_args(argv)

    trabalho = args.trabalho.resolve()
    pagina, roteiro = preparar(trabalho)
    largura, altura, fps = roteiro["largura"], roteiro["altura"], roteiro["fps"]
    total = round(roteiro["duracao"] * fps)

    if not args.quadros and not args.parte:
        # o Chromium gasta quase todo o tempo no PNG de cada quadro: divide em trechos
        n = max(1, args.processos)
        filhos = [
            subprocess.Popen(
                [
                    sys.executable,
                    __file__,
                    "--trabalho",
                    str(trabalho),
                    "--parte",
                    f"{i}/{n}",
                ]
            )
            for i in range(n)
        ]
        codigos = [filho.wait() for filho in filhos]  # espera todos, mesmo se um falhar
        if any(codigos):
            sys.exit("falha em um dos trechos")
        saida = (args.saida or trabalho / "veratus-correntes.mp4").resolve()
        juntar(trabalho, roteiro, n, saida, not args.sem_audio)
        print(saida)
        return 0

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        navegador = p.chromium.launch(args=["--force-color-profile=srgb"])
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
            if (i - inicio) % 60 == 0:
                print(f"parte {indice}: quadro {i - inicio}/{fim - inicio}", flush=True)
        ffmpeg.stdin.close()
        ffmpeg.wait()
        navegador.close()
        return ffmpeg.returncode


if __name__ == "__main__":
    raise SystemExit(main())
