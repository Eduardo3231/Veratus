"""A documentação das rotas acompanha o servidor: toda rota de /os, /integrations e /feeds aparece no agent_api.md."""

from __future__ import annotations

from pathlib import Path

from integrations import webhook

DOC = Path(__file__).resolve().parents[1] / "integrations" / "agent_api.md"


def test_every_route_is_documented():
    doc = DOC.read_text(encoding="utf-8")
    missing = []
    for rule in webhook.app.url_map.iter_rules():
        path = str(rule.rule)
        if not path.startswith(("/os", "/integrations", "/feeds")):
            continue
        # A rota é citada pelo trecho fixo do caminho (antes do primeiro parâmetro)
        # ou por um grupo `/prefixo/*`.
        stem = path.split("<")[0].rstrip("/")
        parts = stem.split("/")
        grouped = any("/".join(parts[:i]) + "/*" in doc for i in range(2, len(parts)))
        if stem not in doc and not grouped:
            missing.append(path)
    assert not missing, missing
