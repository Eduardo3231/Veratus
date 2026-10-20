"""Vídeo das correntes: todo texto que aparece na tela passa no filtro do cliente."""

from __future__ import annotations

import json
import re
from pathlib import Path

from veratus_agents.policy import hard_review_customer_reply

ROTEIRO = Path(__file__).resolve().parents[1] / "video" / "correntes" / "roteiro.json"
PROIBIDO = re.compile(
    r"prata\b|925|\bouro\b|banh|folhead|semi[- ]?joia|zirc|hipoalerg|garantia|"
    r"últim[ao]s?|só hoje|acaba|esgot|fornecedor",
    re.IGNORECASE,
)


def _textos(roteiro: dict) -> list[str]:
    gancho, dif, chat, final = (
        roteiro[k] for k in ("gancho", "diferenciais", "chat", "final")
    )
    textos = [f["texto"] for f in gancho["frases"]]
    for tomada in roteiro["tomadas"]:
        textos += [tomada["titulo"], tomada["sub"]]
    textos += [dif["titulo"], dif["rodape"], *(i["texto"] for i in dif["itens"])]
    textos += [chat["papel"], *(m["texto"] for m in chat["mensagens"])]
    textos += [final["marca"], final["linha"], final["botao"], final["numero"]]
    return [t for t in textos if t]


def test_video_text_passes_the_customer_gate_and_the_timeline_is_continuous():
    roteiro = json.loads(ROTEIRO.read_text(encoding="utf-8"))
    for texto in _textos(roteiro):
        assert not PROIBIDO.search(texto), texto
        assert not hard_review_customer_reply(
            texto, [], product_collection="feminine"
        ), texto
    # O gancho não mostra a marca nem o produto: só depois do estouro entra a primeira foto.
    assert roteiro["tomadas"][0]["t0"] > roteiro["gancho"]["estouro"]
    # Cenas coladas, sem buraco nem sobreposição, até o fim.
    cenas = [*roteiro["tomadas"], roteiro["diferenciais"], roteiro["chat"]]
    for atual, proxima in zip(cenas, [*cenas[1:], roteiro["final"]], strict=True):
        assert atual["t1"] == proxima["t0"]
    assert roteiro["final"]["t0"] < roteiro["duracao"]
