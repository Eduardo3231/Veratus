"""Ficha dos relógios preenchida, para os testes que liberam os canais."""

from __future__ import annotations

from pathlib import Path

import pytest

WATCH_IDS = (
    "arctic-white",
    "ocean-blue",
    "black-gmt",
    "royal-blue",
    "platinum-classic",
    "emerald-signature",
    "silver-prestige",
    "polar-blue",
    "bronze-heritage",
)
FILLED_GENERAL = {
    "envio_do_brasil": "true",
    "cidade_de_envio": "São Paulo/SP",
    "prazo_postagem_dias_uteis": "2",
    "material_caixa": "aço inoxidável",
    "material_pulseira": "aço inoxidável",
    "movimento": "quartzo",
    "resistencia_agua": "3 ATM, resistente a respingos",
    "diametro_caixa_mm": "41",
    "espessura_caixa_mm": "12.5",
    "largura_pulseira_mm": "20",
    "garantia": "90 dias contra defeito de fabricação",
    "embalagem_peso_g": "350",
    "embalagem_comprimento_cm": "18",
    "embalagem_largura_cm": "14",
    "embalagem_altura_cm": "10",
}


def sheet_text(general=None, *, gtin: str | None = "ISENTO", skip=()) -> str:
    """Ficha com tudo CONFIRMADO (ou o que ``general`` disser)."""
    values = {**FILLED_GENERAL, **(general or {})}
    lines = ["schema: veratus.watch_sheet/v1", "geral:"]
    for name, value in values.items():
        status = "PENDENTE" if name in skip or value == "null" else "CONFIRMADO"
        lines += [f"  {name}:", f"    valor: {value}", f"    status: {status}"]
    lines.append("modelos:")
    for model in WATCH_IDS:
        lines.append(f"  {model}:")
        for name, value in (
            ("cor_mostrador", "Azul"),
            ("cor_pulseira", "Prateado"),
            ("gtin", gtin or "null"),
            ("estoque", "5"),
        ):
            status = "PENDENTE" if value == "null" else "CONFIRMADO"
            lines += [
                f"    {name}:",
                f"      valor: {value}",
                f"      status: {status}",
            ]
    return "\n".join(lines) + "\n"


@pytest.fixture
def filled_watch_sheet(tmp_path: Path, monkeypatch) -> Path:
    """Aponta WATCH_SHEET_PATH para uma ficha com tudo confirmado."""
    path = tmp_path / "ficha-relogios.yaml"
    path.write_text(sheet_text(), encoding="utf-8")
    monkeypatch.setenv("WATCH_SHEET_PATH", str(path))
    return path


@pytest.fixture
def watch_facts(filled_watch_sheet, monkeypatch):
    """Instala os fatos da ficha preenchida em CONFIRMED_SALES_FACTS."""
    from veratus_agents import commercial_config
    from veratus_agents.watch_sheet import load_sheet

    facts = load_sheet(filled_watch_sheet).sales_facts({"ocean-blue": "Ocean Blue"})
    for ref, fact in facts.items():
        monkeypatch.setitem(commercial_config.CONFIRMED_SALES_FACTS, ref, fact)
    return facts
