"""Cada canal recebe gestão, marketing, contabilidade e vendas, e a equipe de análise roda."""

from __future__ import annotations

import json
from decimal import Decimal

from veratus_agents.channel_analysis import analisar_canal
from veratus_agents.operations import OperationalRuntime


def _dados():
    pedido = {
        "sku": "VRT-TESTE-01",
        "net_merchandise_brl": "289.90",
        "payment_method": "pix",
    }
    return {
        "env": {"WHATSAPP_SEND_ENABLED": "true"},
        "custos": {"VRT-TESTE-01": Decimal("40.00")},  # custo fictício
        "tipos": {"VRT-TESTE-01": "watch"},
        "conversas": [
            {
                "origin_json": '{"medium": "stories"}',
                "first_inbound_at": "2026-11-04T10:00:00+00:00",
                "first_response_at": "2026-11-04T10:02:00+00:00",
                "discount_requests": 1,
            },
            {"origin_json": "{}", "handoff_reason": "SO_PESSOA"},
        ],
        "eventos": [{"event": "DRAFT_QUEUED"}, {"event": "HANDOFF"}],
        "pedidos": [
            {**pedido, "status": "PAID"},
            {**pedido, "status": "PENDING"},
            {**pedido, "status": "CANCELED"},
        ],
    }


def test_channel_gets_the_four_readings_and_the_team_runs(tmp_path):
    relatorio = analisar_canal("whatsapp", _dados())
    gestao, marketing = relatorio["gestao"], relatorio["marketing"]
    vendas, contas = relatorio["vendas"], relatorio["contabilidade"]
    assert gestao["chaves"]["WHATSAPP_SEND_ENABLED"] is True
    assert gestao["respostas_na_fila"] == 1 and gestao["chamadas_ao_fundador"] == 1
    assert marketing["leads"] == 2 and marketing["origem"]["stories"] == 1
    assert marketing["primeira_resposta_min"] == 2.0
    assert (vendas["pagos"], vendas["pendentes"], vendas["perdidos"]) == (1, 1, 1)
    assert vendas["conversao"] == 0.5 and vendas["ticket_medio"] == "289.90"
    # Só o pago entra na receita; o cancelado aparece à parte.
    assert contas["receita_confirmada"] == "289.90"
    assert contas["cancelado_ou_reembolsado"] == "289.90"
    assert Decimal(contas["taxas_estimadas"]) > 0 and contas["margem"] is not None
    assert "40.00" not in json.dumps(relatorio)  # custo por peça nunca sai
    assert relatorio["recomendacoes"]

    runtime = OperationalRuntime(
        str(tmp_path / "runtime.sqlite3"),
        product_source=list,
        analysis_source=lambda: {"whatsapp": _dados()},
    )
    report = runtime.execute("Gerente, faça a análise de todos os canais.")
    assert report["status"] == "COMPLETED"
    assert set(report["agents_invoked"]) == {
        "general-manager",
        "analysis-supervisor",
        "management-analyst",
        "marketing-analyst",
        "accounting-agent",
        "sales-analyst",
    }
