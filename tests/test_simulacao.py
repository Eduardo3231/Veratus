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
    assert funil["leads"] == 13
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


def test_facebook_is_the_main_marketplace_and_closes_sales(resultado):
    channels = resultado[0]["fluxo"]["channels"]
    # Canais do kit de perfil primeiro; o Facebook é o marketplace principal.
    assert channels[0]["grupo"] == "perfil"
    assert [c["channel"] for c in channels if c["principal"]] == ["facebook"]
    alvo = resultado[0]["rodadas"]["alvo"]
    facebook = [c for c in alvo["conversas"] if c["canal"] == "facebook"]
    assert len(facebook) == 3
    assert sum(1 for c in facebook if c["desfecho"] == "enviado") == 2
    assert alvo["metricas_por_canal"]["facebook"]["orders_paid"] == 2
    replies = [
        item["texto"]
        for item in alvo["linha_do_tempo"]
        if item["canal"] == "facebook" and item["tipo"] == "resposta_enviada"
    ]
    # "Ainda está disponível?" não recebe promessa de estoque.
    assert replies and "disponível" not in replies[0].lower()


def test_stories_lead_closes_on_whatsapp_as_the_profile_kit_says(resultado):
    alvo, hoje = (resultado[0]["rodadas"][name] for name in ("alvo", "hoje"))
    [cliente] = [c for c in alvo["conversas"] if c["conversa"] == "c13"]
    assert (cliente["desfecho"], cliente["canais"]) == (
        "enviado",
        ["instagram", "whatsapp"],
    )
    assert alvo["harvey"]["instagram_para_whatsapp"] == {
        "levados": 1,
        "levados_dos_stories": 1,
        "chegaram": 1,
        "chegaram_dos_stories": 1,
        "pedidos": 1,
        "pagos": 1,
    }
    # Em SHADOW o link fica em rascunho: ninguém chega ao WhatsApp.
    assert hoje["harvey"]["instagram_para_whatsapp"]["chegaram"] == 0


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
    assert "80010-010" not in dump and "Rua XV de Novembro" not in dump
    assert "70040-010" not in dump and "Eixo Monumental" not in dump
    for cliente in simular_operacao.clientes().values():
        if cliente.psid:
            assert cliente.psid not in dump
        if cliente.ig_id:
            assert cliente.ig_id not in dump
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
