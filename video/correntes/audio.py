"""Sintetiza a trilha do vídeo das correntes (sem amostra de terceiros, só ondas e ruído).

Os tempos vêm de ``roteiro.json``: o tique-taque acelera até o estouro, as notas de sino
marcam cada tomada e o acorde final fecha na chamada. Saída: WAV estéreo de 44,1 kHz.
"""

from __future__ import annotations

import math
import random
import struct
import wave
from pathlib import Path

SR = 44100
TAU = 2 * math.pi


class Mix:
    def __init__(self, segundos: float):
        self.n = int(segundos * SR)
        self.l = [0.0] * self.n
        self.r = [0.0] * self.n

    def add(
        self, inicio: float, amostras: list[float], ganho: float = 1.0, pan: float = 0.0
    ) -> None:
        i0 = int(inicio * SR)
        gl, gr = ganho * (1 - max(pan, 0)), ganho * (1 + min(pan, 0))
        l, r, n = self.l, self.r, self.n
        for k, v in enumerate(amostras):
            i = i0 + k
            if i >= n:
                break
            if i >= 0:
                l[i] += v * gl
                r[i] += v * gr


def tom(
    freq: float,
    dur: float,
    tau: float,
    ataque: float = 0.004,
    parciais=((1, 1.0),),
    queda: float = 0.0,
):
    """Nota com ataque linear e queda exponencial; ``queda`` desliza a altura (Hz/s)."""
    out, fase = [], 0.0
    for k in range(int(dur * SR)):
        t = k / SR
        env = min(1.0, t / ataque) * math.exp(-t / tau)
        f = max(20.0, freq - queda * t)
        fase += TAU * f / SR
        out.append(env * sum(g * math.sin(fase * m) for m, g in parciais))
    return out


def ruido(dur: float, tau: float, lp: float = 1.0, rng: random.Random | None = None):
    rng = rng or random.Random(1)
    out, y = [], 0.0
    for k in range(int(dur * SR)):
        y += lp * (rng.uniform(-1, 1) - y)
        out.append(y * math.exp(-(k / SR) / tau))
    return out


def sino(freq: float, tau: float = 0.7):
    return tom(
        freq, tau * 5, tau, 0.002, ((1, 1.0), (2.76, 0.38), (5.4, 0.14), (8.9, 0.05))
    )


def sintetizar(roteiro: dict, destino: Path) -> Path:
    rng = random.Random(5)
    dur = roteiro["duracao"]
    g = roteiro["gancho"]
    pop = g["estouro"]
    m = Mix(dur + 0.2)

    # gancho: zumbido que sobe, ruído que abre e tique-taque que acelera
    fase, y, rise = 0.0, 0.0, []
    for k in range(int(pop * SR)):
        t = k / SR
        u = t / pop
        fase += TAU * (46 * 2 ** (1.4 * u)) / SR
        y += (0.02 + 0.85 * u * u) * (rng.uniform(-1, 1) - y)
        rise.append(
            (0.05 + 0.4 * u * u) * (math.sin(fase) + 0.4 * math.sin(2 * fase))
            + (0.015 + 0.22 * u**3) * y
        )
    m.add(0.0, rise, 0.9)
    t = 0.2
    while t < pop - 0.05:
        u = t / pop
        m.add(
            t,
            tom(2300, 0.05, 0.012, 0.0005, ((1, 1.0), (2, 0.3))),
            0.16 + 0.22 * u,
            pan=0.12 * math.sin(t * 9),
        )
        t += 0.5 - 0.38 * u**1.3
    for c in g["contagem"]:
        m.add(
            c["t0"], tom(150, 0.22, 0.07, 0.002, ((1, 1.0), (2, 0.25)), queda=260), 0.55
        )
    # estouro
    m.add(pop, ruido(0.55, 0.11, 0.55, rng), 1.0)
    m.add(pop, tom(80, 0.9, 0.22, 0.001, ((1, 1.0), (2, 0.2)), queda=95), 0.95)
    for _ in range(26):
        f = rng.uniform(2200, 7600)
        m.add(
            pop + rng.uniform(0, 0.14),
            tom(f, 1.4, rng.uniform(0.25, 0.7), 0.002),
            rng.uniform(0.018, 0.05),
            pan=rng.uniform(-0.7, 0.7),
        )

    # base harmônica do resto do vídeo (Ré maior com sétima e nona)
    t0 = pop + 0.1
    seg = dur - t0
    for freq, ganho in (
        (73.42, 0.07),
        (146.83, 0.05),
        (220.0, 0.04),
        (277.18, 0.032),
        (329.63, 0.03),
        (369.99, 0.024),
    ):
        pad, fase = [], rng.random() * TAU
        for k in range(int(seg * SR)):
            t = k / SR
            env = (
                min(1.0, t / 1.4)
                * min(1.0, (seg - t) / 1.3)
                * (0.82 + 0.18 * math.sin(TAU * 0.17 * t + freq))
            )
            fase += TAU * freq / SR
            pad.append(env * (math.sin(fase) + 0.25 * math.sin(2 * fase)))
        m.add(t0, pad, ganho, pan=rng.uniform(-0.3, 0.3))
    notas = (1318.5, 880.0, 1479.98, 1174.66)
    for i, s in enumerate([*roteiro["tomadas"], roteiro["diferenciais"]]):
        m.add(s["t0"], sino(notas[i % 4], 0.55), 0.16, pan=0.2 if i % 2 else -0.2)
    for it in roteiro["diferenciais"]["itens"]:
        m.add(it["t"], tom(1760, 0.25, 0.09, 0.002, ((1, 1.0), (2, 0.2))), 0.17)
        m.add(it["t"] + 0.07, tom(2349, 0.25, 0.09, 0.002), 0.11)

    # conversa
    for msg in roteiro["chat"]["mensagens"]:
        cliente = msg["de"] == "cliente"
        a, b = (740, 988) if cliente else (988, 1318)
        m.add(msg["t"], tom(a, 0.09, 0.04, 0.002), 0.2)
        m.add(msg["t"] + 0.08, tom(b, 0.18, 0.07, 0.002), 0.22)
        if "digitando" in msg:
            for k in range(3):
                m.add(
                    msg["digitando"] + 0.15 + 0.18 * k,
                    tom(1500 + 120 * k, 0.04, 0.012, 0.0005),
                    0.07,
                )

    # final
    f = roteiro["final"]
    for freq, ganho in (
        (293.66, 0.07),
        (369.99, 0.055),
        (440.0, 0.05),
        (587.33, 0.045),
    ):
        m.add(
            f["t0"],
            tom(freq, dur - f["t0"], 2.6, 0.9, ((1, 1.0), (2, 0.2))),
            ganho,
            pan=rng.uniform(-0.25, 0.25),
        )
    for _ in range(34):
        m.add(
            f["t0"] + rng.uniform(0.05, 0.9),
            tom(rng.uniform(2600, 7200), 1.2, rng.uniform(0.2, 0.55), 0.002),
            rng.uniform(0.012, 0.035),
            pan=rng.uniform(-0.7, 0.7),
        )
    m.add(f["t_marca"], sino(587.33, 1.0), 0.2)
    m.add(f["t_linha"], sino(880.0, 0.8), 0.12, pan=0.15)
    m.add(f["t_botao"], sino(1318.5, 0.9), 0.24, pan=-0.1)
    m.add(f["t_botao"] + 0.06, sino(1760.0, 0.9), 0.16, pan=0.1)

    # mixagem final: limitador suave, entrada e saída curtas
    pico = max(max(map(abs, m.l)), max(map(abs, m.r)), 1e-9)
    esc = 0.9 / pico
    saida = bytearray()
    for i in range(m.n):
        t = i / SR
        fade = min(1.0, t / 0.01) * min(
            1.0, max(0.0, (dur - t)) / 0.5 if t > dur - 0.5 else 1.0
        )
        for canal in (m.l, m.r):
            v = math.tanh(1.25 * canal[i] * esc) / math.tanh(1.25) * fade
            saida += struct.pack("<h", int(max(-1, min(1, v)) * 32767))
    destino.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(destino), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(bytes(saida))
    return destino


if __name__ == "__main__":
    import json
    import sys

    aqui = Path(__file__).resolve().parent
    alvo = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else aqui.parents[1] / "runtime/video/correntes/audio.wav"
    )
    print(
        sintetizar(
            json.loads((aqui / "roteiro.json").read_text(encoding="utf-8")), alvo
        )
    )
