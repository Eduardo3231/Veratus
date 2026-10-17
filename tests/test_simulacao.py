"""Modelo virtual da operação: o código real, sem rede e sem dado pessoal na saída."""

from __future__ import annotations

import json
import re
import socket
from unittest.mock import patch

import pytest

from scripts import simular_operacao


def _no_network(*args, **kwargs):
    raise AssertionError("a simulação tentou abrir uma conexão de rede real")


@pytest.fixture(scope="module")
def resultado():
    with (
        patch.object(socket.socket, "connect", _no_network),
        patch.object(socket, "create_connection", _no_network),
    ):
        primeira = simular_operacao.simular()
        segunda = simular_operacao.simular()
    return primeira, segunda


def test_the_day_goes_from_lead_to_paid_and_shipped_orders(resultado):
    alvo = resultado[0]["rodadas"]["alvo"]
    funil = alvo["funil"]
    assert funil["leads"] == 9
    assert funil["pedidos"] >= 3
    assert funil["pagos"] >= 3
    assert funil["enviados"] >= 3
    assert funil["respostas_enviadas"] > 0


def test_the_founder_is_called_only_when_the_client_asks_or_on_a_legal_threat(
    resultado,
):
    for rodada in resultado[0]["rodadas"].values():
        reasons = set()
        for canal in rodada["metricas_por_canal"].values():
            reasons |= set(canal["handoffs_by_reason"])
        assert reasons <= {"PEDIDO_DE_ATENDENTE", "RECLAMACAO"}
        assert rodada["funil"]["chamadas_ao_fundador"] == 2


def test_today_nothing_leaves_the_machine(resultado):
    hoje = resultado[0]["rodadas"]["hoje"]
    assert hoje["funil"]["respostas_enviadas"] == 0
    assert hoje["funil"]["pagos"] == 0
    # Com os padrões de produção só há leitura: nenhum POST para fora.
    assert all(item["chamada"].startswith("GET ") for item in hoje["chamadas_http"])


def test_marketplace_answers_carry_no_contact_or_direct_sale_terms(resultado):
    forbidden = re.compile(
        r"whats|telefone|@|https?://|\bsite\b|7 dias|R\$|\bpix\b", re.IGNORECASE
    )
    alvo = resultado[0]["rodadas"]["alvo"]
    answers = [
        item["texto"]
        for item in alvo["linha_do_tempo"]
        if item["canal"] in ("mercado_livre", "shopee") and item["ator"] != "cliente"
    ]
    assert answers
    for text in answers:
        assert not forbidden.search(text), text


def test_output_has_no_personal_data(resultado):
    dump = json.dumps(resultado[0], ensure_ascii=False)
    for cliente in simular_operacao.clientes().values():
        if cliente.numero:
            assert cliente.numero not in dump
    assert simular_operacao.FOUNDER not in dump
    assert "01001-000" not in dump and "Praça da Sé" not in dump
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", dump)


def test_two_runs_give_the_same_day(resultado):
    primeira, segunda = resultado
    for nome in ("alvo", "hoje"):
        assert (
            primeira["rodadas"][nome]["linha_do_tempo"]
            == segunda["rodadas"][nome]["linha_do_tempo"]
        )


def test_cli_writes_the_json(tmp_path):
    saida = tmp_path / "operacao.json"
    with patch.object(simular_operacao, "simular", return_value={"rodadas": {}}):
        assert simular_operacao.main(["--saida", str(saida)]) == 0
    assert json.loads(saida.read_text(encoding="utf-8")) == {"rodadas": {}}
