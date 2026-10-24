"""Vídeo do sniper: todo texto da tela passa no filtro do cliente e a linha do tempo fecha."""

from __future__ import annotations

import json
import re
from itertools import pairwise
from pathlib import Path

from veratus_agents.policy import hard_review_customer_reply

ROTEIRO = Path(__file__).resolve().parents[1] / "video" / "sniper" / "roteiro.json"
PROIBIDO = re.compile(
    r"prata\b|925|\bouro\b|banh|folhead|semi[- ]?joia|zirc|hipoalerg|garantia|"
    r"últim[ao]s?|só hoje|acaba|esgot|fornecedor|"
    r"barrett|remington|lapua|accuracy|sako|taurus|cbc",  # marca de arma ou de munição
    re.IGNORECASE,
)


def test_sniper_video_text_passes_the_customer_gate_and_the_timeline_closes():
    r = json.loads(ROTEIRO.read_text(encoding="utf-8"))
    ab, bala, gancho, emb, como, final = (
        r[k] for k in ("abertura", "bala", "gancho", "emblema", "como", "final")
    )
    textos = [ab[k]["texto"] for k in ("atirador", "alvo", "mira")]
    textos += [f["texto"] for f in bala["frases"]]
    textos += [gancho["linha"], gancho["sub"], emb["marca"], emb["sub"]]
    for tomada in r["tomadas"]:
        textos += [tomada["titulo"], tomada["sub"]]
    textos += [como["titulo"], como["rodape"]]
    for item in como["itens"]:
        textos += [item["texto"], item["sub"]]
    textos += [final["marca"], final["linha"], final["botao"], final["numero"]]
    for texto in filter(None, textos):
        assert not PROIBIDO.search(texto), texto
        assert not hard_review_customer_reply(
            texto, [], product_collection="feminine"
        ), texto
    # Abertura sem marca nem produto: a Veratus só entra depois que a bala congela.
    abertura = [ab[k] for k in ("atirador", "alvo", "mira", "disparo")]
    for atual, proxima in pairwise(abertura):
        assert atual["t1"] == proxima["t0"]
    assert ab["disparo"]["t1"] == bala["t0"] < bala["contato"] < bala["congela"]
    assert bala["congela"] == gancho["t0"] and gancho["t1"] == emb["t0"]
    cenas = [emb, *r["tomadas"], como]
    for atual, proxima in zip(cenas, [*cenas[1:], final], strict=True):
        assert atual["t1"] == proxima["t0"]
    assert final["t0"] < r["duracao"]
