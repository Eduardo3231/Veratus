"""Sintetiza a trilha do vídeo do sniper (sem amostra de terceiros, só ondas e ruído).

Os tempos vêm de ``roteiro.json`` e a curva da bala é a mesma do ``cena.html``: o assobio desce
de tom e o coração bate mais devagar conforme a bala desacelera; um tique marca cada vez que a
distância cai dez vezes. No congelamento tudo para e fica só o vidro vibrando. As ferramentas
básicas (mixagem, nota, ruído e sino) são as do vídeo das correntes. Saída: WAV estéreo 44,1 kHz.
"""

from __future__ import annotations

import importlib.util
import math
import random
import struct
import wave
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "audio_correntes", Path(__file__).resolve().parents[1] / "correntes" / "audio.py"
)
_base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_base)
SR, TAU, Mix, tom, ruido, sino = (
    _base.SR,
    _base.TAU,
    _base.Mix,
    _base.tom,
    _base.ruido,
    _base.sino,
)


def passa_baixa(x: list[float], corte: float) -> list[float]:
    a, y, out = 1 - math.exp(-TAU * corte / SR), 0.0, []
    for v in x:
        y += a * (v - y)
        out.append(y)
    return out


def passa_alta(x: list[float], corte: float) -> list[float]:
    return [v - w for v, w in zip(x, passa_baixa(x, corte), strict=True)]


def curva(b: dict):
    """Distância e velocidade da bala no tempo do vídeo (a mesma conta do cena.html)."""
    dist0, v0, eps, t0 = b["distancia_m"], b["velocidade_ms"], b["folga_m"], b["t0"]
    lo, hi = 0.1, 20.0
    for _ in range(80):
        k = (lo + hi) / 2
        total = (dist0 - v0 / k + eps) / v0 + math.log(v0 / (k * eps)) / k
        lo, hi = (k, hi) if total > b["contato"] - t0 else (lo, k)
    k = (lo + hi) / 2
    s1 = (dist0 - v0 / k + eps) / v0

    def dist(t: float) -> float:
        s = t - t0
        if s <= 0:
            return dist0
        if s < s1:
            return dist0 - v0 * s
        return max(0.0, v0 / k * math.exp(-k * (s - s1)) - eps)

    def vel(t: float) -> float:
        return v0 if t - t0 < s1 else k * (dist(t) + eps)

    def quando(d: float) -> float:
        if d >= v0 / k - eps:
            return t0 + (dist0 - d) / v0
        return t0 + s1 + math.log(v0 / k / (d + eps)) / k

    return dist, vel, quando


def sintetizar(roteiro: dict, destino: Path) -> Path:
    rng = random.Random(9)
    dur, ab, b = roteiro["duracao"], roteiro["abertura"], roteiro["bala"]
    m = Mix(dur + 0.3)
    tiro, sil = ab["disparo"]["t0"], ab["mira"]["silencio"]
    tc, tf, retoma = b["contato"], b["congela"], roteiro["emblema"]["t0"]
    _, vel, quando = curva(b)
    v0 = b["velocidade_ms"]

    # abertura: vento com rajadas e um grave que sobe até o silêncio antes do tiro
    n = int(sil * SR)
    vento = passa_baixa([rng.uniform(-1, 1) for _ in range(n)], 380)
    grave, f1 = [], 0.0
    for i in range(n):
        t = i / SR
        borda = min(1.0, t / 0.4) * min(1.0, (sil - t) / 0.06)
        vento[i] *= (
            (0.55 + 0.45 * math.sin(t * 1.3) * math.sin(t * 0.47 + 1)) * borda * 2.4
        )
        f1 += TAU * 55 / SR
        sobe = 0.25 + 0.75 * (t / sil) ** 2
        grave.append(
            sobe
            * borda
            * (math.sin(f1) + 0.5 * math.sin(1.5 * f1) + 0.25 * math.sin(2 * f1))
        )
    m.add(0.0, vento, 0.45, pan=-0.2)
    m.add(0.013, vento, 0.32, pan=0.3)
    m.add(0.0, grave, 0.2)
    for t in (ab["atirador"]["t0"], ab["alvo"]["t0"], ab["mira"]["t_texto"]):
        m.add(t, tom(46, 1.4, 0.4, 0.002, ((1, 1.0), (2, 0.3)), queda=10), 0.55)
    for _ in range(220):  # areia escorrendo na ampulheta
        t = ab["alvo"]["t0"] + rng.random() * (ab["alvo"]["t1"] - ab["alvo"]["t0"])
        m.add(
            t,
            tom(rng.uniform(3000, 7000), 0.012, 0.002, 0.0005),
            0.012,
            rng.uniform(-0.4, 0.4),
        )

    # mira: respiração, coração e os cliques do ajuste da torre
    for t0, t1, corte in ((3.65, 4.3, 1300), (4.35, 5.1, 900)):
        sopro = passa_alta(passa_baixa(ruido(t1 - t0, 99, 1.0, rng), corte), 280)
        for i in range(len(sopro)):
            sopro[i] *= math.sin(math.pi * i / len(sopro)) ** 1.5
        m.add(t0, sopro, 0.5)
    t = ab["mira"]["t0"] + 0.1
    while t < sil - 0.2:
        m.add(t, tom(55, 0.2, 0.05, 0.004, ((1, 1.0), (2, 0.2))), 0.5)
        m.add(t + 0.22, tom(46, 0.2, 0.05, 0.004), 0.32)
        t += 0.84
    for c in ab["mira"]["cliques"]:
        m.add(c, passa_alta(ruido(0.006, 0.0012, 1.0, rng), 2500), 0.35)
        m.add(c, tom(3400, 0.03, 0.006, 0.0003), 0.12)
        m.add(c + 0.004, tom(5600, 0.02, 0.004, 0.0003), 0.07)

    # o tiro: estalo supersônico, corpo, grave, ecos nas montanhas e a cauda que rola no vale
    m.add(tiro, passa_alta(ruido(0.03, 0.004, 1.0, rng), 1800), 1.0)
    m.add(tiro, passa_baixa(ruido(0.5, 0.07, 1.0, rng), 900), 1.3)
    m.add(tiro, passa_baixa(ruido(1.2, 0.22, 1.0, rng), 160), 1.6)
    m.add(tiro, tom(58, 1.4, 0.32, 0.001, ((1, 1.0), (2, 0.25)), queda=18), 1.2)
    eco = passa_baixa(ruido(0.9, 0.16, 1.0, rng), 700)
    for atraso, ganho, corte, pan in (
        (0.42, 0.55, 1100, -0.55),
        (0.93, 0.4, 800, 0.6),
        (1.52, 0.3, 600, -0.4),
        (2.25, 0.21, 480, 0.45),
        (3.1, 0.14, 380, -0.25),
        (4.15, 0.09, 300, 0.3),
    ):
        m.add(tiro + atraso, passa_baixa(eco, corte), ganho, pan=pan)
    m.add(tiro + 0.05, passa_baixa(ruido(5.5, 1.8, 1.0, rng), 120), 1.1)
    for k in range(14):  # asas e pássaros saindo das árvores
        m.add(
            tiro + 0.35 + 0.07 * k + rng.random() * 0.02,
            passa_baixa(ruido(0.05, 0.012, 1.0, rng), 900),
            0.08,
            pan=rng.uniform(-0.6, 0.2),
        )
    for _ in range(12):
        t = tiro + 0.45 + rng.random() * 1.2
        m.add(
            t,
            tom(rng.uniform(3200, 4800), 0.09, 0.03, 0.003, queda=9000),
            0.035,
            rng.uniform(-0.8, 0.8),
        )

    # a bala: assobio que desce de tom, ar que fecha e um grave de câmera lenta que cresce
    t_b = b["t0"]
    saida, fase, ar = [], 0.0, 0.0
    for i in range(int((tc - t_b) * SR)):
        t = t_b + i / SR
        ts = vel(t) / v0
        fase += TAU * (60 + 1700 * ts**0.2) / SR
        a = 1 - math.exp(-TAU * (180 + 5200 * ts**0.3) / SR)
        ar += a * (rng.uniform(-1, 1) - ar)
        lento = 1 - ts**0.1
        drone = (
            math.sin(TAU * 41 * t)
            + 0.5 * math.sin(TAU * 61.5 * t + 1)
            + 0.3 * math.sin(TAU * 82 * t)
        )
        saida.append(
            min(1.0, (t - t_b) / 0.02)
            * (
                0.2 * ts**0.12 * (math.sin(fase) + 0.3 * math.sin(2 * fase))
                + ar * (0.4 * ts**0.1 + 0.06)
                + 0.32 * lento * drone
            )
        )
    m.add(t_b, saida, 0.6)
    t = t_b + 0.1
    while t < tc:  # o coração desacelera junto com a bala
        m.add(t, tom(52, 0.25, 0.07, 0.004, ((1, 1.0), (2, 0.2))), 0.45)
        m.add(t + 0.24, tom(44, 0.25, 0.07, 0.004), 0.3)
        t += 0.62 / max(vel(t) / v0, 1e-9) ** 0.07
    for j, d in enumerate((1000, 100, 10, 1, 0.1, 0.01, 0.001)):
        f = 2400 * 0.84**j
        m.add(quando(d), tom(f, 0.14, 0.035, 0.001, ((1, 1.0), (2.7, 0.3))), 0.12)
    corte = quando(b["macro_m"])
    m.add(
        corte - 0.05,
        passa_alta(passa_baixa(ruido(0.3, 0.08, 1.0, rng), 3000), 400),
        0.3,
    )

    # contato: o vidro estala em câmera lenta, com o grave e a areia
    m.add(tc, tom(36, 1.1, 0.45, 0.02, ((1, 1.0), (2, 0.3)), queda=8), 1.0)
    estalo = passa_baixa(ruido(tf - tc + 0.2, 0.6, 1.0, rng), 700)
    for i in range(len(estalo)):
        estalo[i] *= min(1.0, i / (0.08 * SR))
    m.add(tc, estalo, 0.7)
    for _ in range(140):
        t = tc + (tf - tc) * rng.random() ** 0.7
        m.add(
            t,
            tom(rng.uniform(250, 1100), 0.06, 0.02, 0.001),
            rng.uniform(0.05, 0.12),
            rng.uniform(-0.6, 0.6),
        )

    # congelamento: corta tudo e deixa o vidro vibrando sob a pergunta
    i0, i1 = int(tf * SR), int(retoma * SR)
    for i in range(i0, min(i1, m.n)):
        g = max(0.0, 1 - (i - i0) / (0.004 * SR))
        m.l[i] *= g
        m.r[i] *= g
    m.add(tf, tom(42, 1.4, 0.4, 0.001, ((1, 1.0), (2, 0.4)), queda=10), 0.8)
    m.add(
        tf,
        tom(2637, 3.4, 1.6, 0.003, ((1, 1.0), (1.0015, 0.9), (1.5, 0.3), (2, 0.12))),
        0.09,
    )
    for freq in (110.0, 220.0, 261.63, 329.63):
        m.add(
            tf + 0.3,
            tom(freq, retoma - tf - 0.3, 6.0, 1.2),
            0.03,
            pan=rng.uniform(-0.3, 0.3),
        )
    m.add(roteiro["gancho"]["t_linha"], sino(880.0, 0.9), 0.08)
    m.add(roteiro["gancho"]["t_sub"], sino(1318.5, 0.8), 0.06, pan=0.2)

    # retomada: sopro ao contrário, estilhaços, a areia que brilha e vira o emblema
    n = int(0.45 * SR)
    sopro, y = [], 0.0
    for i in range(n):
        u = i / n
        y += (1 - math.exp(-TAU * (200 + 6000 * u * u) / SR)) * (rng.uniform(-1, 1) - y)
        sopro.append(y * u**3)
    m.add(retoma - 0.45, sopro, 0.5)
    m.add(retoma, passa_alta(ruido(0.4, 0.12, 1.0, rng), 1500), 0.35)
    for _ in range(40):
        t = retoma + rng.random() * 0.6
        m.add(
            t,
            tom(rng.uniform(3000, 8000), 0.5, rng.uniform(0.05, 0.2), 0.002),
            rng.uniform(0.02, 0.05),
            rng.uniform(-0.7, 0.7),
        )
    for _ in range(90):
        t = retoma + 0.1 + rng.random() ** 0.6 * 1.0
        m.add(
            t,
            tom(rng.uniform(2500, 7000), 0.3, rng.uniform(0.08, 0.25), 0.002),
            rng.uniform(0.01, 0.03),
            rng.uniform(-0.7, 0.7),
        )

    # base harmônica de Ré maior do emblema até o fim, como no vídeo das correntes
    e, t0 = roteiro["emblema"], roteiro["emblema"]["t0"] + 1.0
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
    m.add(t0, sino(587.33, 1.1), 0.2)
    m.add(e["t_marca"], sino(880.0, 0.9), 0.12, pan=0.15)
    notas = (1318.5, 880.0, 1479.98, 1174.66)
    for i, s in enumerate([*roteiro["tomadas"], roteiro["como"]]):
        m.add(s["t0"], sino(notas[i % 4], 0.55), 0.16, pan=0.2 if i % 2 else -0.2)
    for it in roteiro["como"]["itens"]:
        m.add(it["t"], passa_alta(ruido(0.005, 0.0012, 1.0, rng), 2500), 0.15)
        m.add(it["t"], tom(1760, 0.25, 0.09, 0.002, ((1, 1.0), (2, 0.2))), 0.17)
        m.add(it["t"] + 0.07, tom(2349, 0.25, 0.09, 0.002), 0.11)

    # chamada final
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
        t = f["t0"] + rng.uniform(0.05, 0.9)
        m.add(
            t,
            tom(rng.uniform(2600, 7200), 1.2, rng.uniform(0.2, 0.55), 0.002),
            rng.uniform(0.012, 0.035),
            rng.uniform(-0.7, 0.7),
        )
    m.add(f["t_marca"], sino(587.33, 1.0), 0.2)
    m.add(f["t_linha"], sino(880.0, 0.8), 0.12, pan=0.15)
    m.add(f["t_botao"], sino(1318.5, 0.9), 0.24, pan=-0.1)
    m.add(f["t_botao"] + 0.06, sino(1760.0, 0.9), 0.16, pan=0.1)

    # mixagem: o volume se acerta pelo resto do vídeo e o tiro passa pelo limitador suave,
    # que achata o pico e deixa o disparo mais cheio
    fora = [i for i in range(m.n) if not tiro - 0.01 <= i / SR <= tiro + 0.6]
    pico = max(max(abs(m.l[i]), abs(m.r[i])) for i in fora) or 1e-9
    esc = 0.8 / pico
    out = bytearray()
    for i in range(m.n):
        t = i / SR
        fade = min(1.0, t / 0.01) * (
            min(1.0, max(0.0, dur - t) / 0.5) if t > dur - 0.5 else 1.0
        )
        # o fim sobe 3,5 dB
        fade *= 1 + 0.5 * min(1.0, max(0.0, (t - retoma + 0.5) / 0.5))
        for canal in (m.l, m.r):
            v = 0.88 * math.tanh(1.4 * canal[i] * esc) / math.tanh(1.4) * fade
            out += struct.pack("<h", int(max(-1, min(1, v)) * 32767))
    destino.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(destino), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(bytes(out))
    return destino


if __name__ == "__main__":
    import json
    import sys

    aqui = Path(__file__).resolve().parent
    alvo = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else aqui.parents[1] / "runtime/video/sniper/audio.wav"
    )
    print(
        sintetizar(
            json.loads((aqui / "roteiro.json").read_text(encoding="utf-8")), alvo
        )
    )
