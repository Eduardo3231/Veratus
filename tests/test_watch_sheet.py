"""Ficha dos relógios: o fundador preenche e os canais liberam sozinhos."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from conftest import sheet_text
from cryptography.fernet import Fernet

from veratus_agents import watch_sheet
from veratus_agents.conversations import (
    ConversationEngine,
    ConversationStore,
    InboundText,
)
from veratus_agents.policy import hard_review_customer_reply


def write(tmp_path, text):
    path = tmp_path / "ficha.yaml"
    path.write_text(text, encoding="utf-8")
    return path


# -- a ficha do repositório -------------------------------------------------------


def test_repository_sheet_starts_all_pending():
    sheet = watch_sheet.load_sheet()

    assert sheet.origin_confirmed() is False
    assert len(sheet.pending_general()) == len(watch_sheet.GENERAL_FIELDS)
    assert sheet.sales_facts() == {}
    assert set(sheet.models) == {
        "arctic-white",
        "ocean-blue",
        "black-gmt",
        "royal-blue",
        "platinum-classic",
        "emerald-signature",
        "silver-prestige",
        "polar-blue",
        "bronze-heritage",
    }


def test_report_shows_watches_on_sale_and_what_each_channel_needs():
    report = watch_sheet.readiness_report()

    assert report["ready_by_channel"]["site"] == "9/9"
    assert report["ready_by_channel"]["whatsapp"] == "9/9"
    assert report["ready_by_channel"]["mercado_livre"] == "0/9"
    ocean = report["models"]["ocean-blue"]["channels"]
    assert ocean["instagram_post"] == ["NEEDS_REAL_PHOTO"]
    assert "ORIGIN_UNCONFIRMED" in ocean["shopee"]
    assert "envio_do_brasil" in report["general_pending"]
    assert report["economics"]["mercado_livre"]["status"] == "BLOCKED_BY_ORIGIN"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("  campo_novo:\n    valor: 1\n    status: CONFIRMADO\n", "campo desconhecido"),
        (
            "  movimento:\n    valor: null\n    status: CONFIRMADO\n",
            "CONFIRMADO sem valor",
        ),
        (
            "  movimento:\n    valor: quartzo\n    status: TALVEZ\n",
            "PENDENTE ou CONFIRMADO",
        ),
        (
            "  envio_do_brasil:\n    valor: sim\n    status: CONFIRMADO\n",
            "true ou false",
        ),
        ("  embalagem_peso_g:\n    valor: 35.5\n    status: CONFIRMADO\n", "inteiro"),
    ],
)
def test_invalid_sheet_says_which_field_to_fix(tmp_path, change, message):
    text = "schema: veratus.watch_sheet/v1\ngeral:\n" + change + "modelos: {}\n"

    with pytest.raises(watch_sheet.WatchSheetError, match=message):
        watch_sheet.load_sheet(write(tmp_path, text))


def test_bad_barcode_and_unknown_model_are_rejected(tmp_path):
    bad_gtin = sheet_text(gtin="123")
    with pytest.raises(watch_sheet.WatchSheetError, match="código de barras"):
        watch_sheet.load_sheet(write(tmp_path, bad_gtin))
    unknown = "schema: veratus.watch_sheet/v1\ngeral: {}\nmodelos:\n  rolex: {}\n"
    with pytest.raises(watch_sheet.WatchSheetError, match="id desconhecido"):
        watch_sheet.load_sheet(write(tmp_path, unknown))


# -- preenchida, ela libera --------------------------------------------------------


def test_filled_sheet_releases_every_channel_with_real_photo(filled_watch_sheet):
    sheet = watch_sheet.load_sheet(filled_watch_sheet)

    assert sheet.origin_confirmed() is True
    for channel in watch_sheet.CHANNELS:
        assert sheet.missing("ocean-blue", channel, real_photo=True) == []
    assert sheet.missing("ocean-blue", "amazon", real_photo=False) == [
        "NEEDS_REAL_PHOTO"
    ]
    attributes = sheet.listing_attributes("ocean-blue")
    assert attributes["espessura_caixa_mm"] == "12.5"
    assert attributes["estoque"] == 5


def test_amazon_needs_the_barcode(tmp_path):
    sheet = watch_sheet.load_sheet(write(tmp_path, sheet_text(gtin=None)))

    assert sheet.missing("ocean-blue", "shopee", real_photo=True) == []
    assert sheet.missing("ocean-blue", "amazon", real_photo=True) == [
        "FICHA:ocean-blue.gtin"
    ]


def test_origin_false_keeps_marketplaces_waiting(tmp_path):
    sheet = watch_sheet.load_sheet(
        write(tmp_path, sheet_text({"envio_do_brasil": "false"}))
    )

    assert sheet.origin_confirmed() is False
    assert sheet.missing("ocean-blue", "mercado_livre", real_photo=True) == [
        "ORIGIN_UNCONFIRMED"
    ]
    assert "relogios-envio" not in sheet.sales_facts()


def test_confirmed_fields_become_sales_facts(watch_facts):
    facts = watch_facts

    assert facts["relogios-diametro"]["text"] == "Caixa de 41 mm de diâmetro."
    assert facts["relogios-espessura"]["text"] == "Caixa de 12,5 mm de espessura."
    assert facts["relogios-movimento"]["text"] == "Movimento quartzo."
    assert facts["relogios-garantia"]["claim_type"] == "warranty"
    assert (
        facts["relogios-envio"]["text"] == "Os relógios são enviados de São Paulo/SP."
    )
    assert facts["ocean-blue-cores"]["text"] == (
        "O Ocean Blue tem mostrador em tom azul e pulseira em tom prateado."
    )
    # Toda frase que o atendimento tira da ficha passa no gate.
    for fact in facts.values():
        assert fact["scope"] == "watches"
        assert (
            hard_review_customer_reply(fact["text"], product_collection="watches") == []
        )


def test_knowledge_merges_the_sheet_facts(filled_watch_sheet):
    from veratus_agents import knowledge

    merged = knowledge.confirmed_sales_facts({})

    assert "preco-relogios" in merged
    assert merged["relogios-movimento"]["gate_issue"] == (
        "especificação técnica não validada"
    )


# -- o gate libera só o texto confirmado ------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "A caixa tem 41 mm de diâmetro.",
        "O movimento é quartzo.",
        "Tem garantia de 90 dias contra defeito de fabricação.",
        "Resistência à água: 3 ATM, resistente a respingos.",
        "A caixa tem 12.5 mm de espessura.",
    ],
)
def test_gate_accepts_the_confirmed_statement(watch_facts, text):
    assert hard_review_customer_reply(text, product_collection="watches") == []


@pytest.mark.parametrize(
    "text",
    [
        "A caixa tem 40 mm de diâmetro.",
        "O movimento é automático.",
        "Garantia de 1 ano.",
        "Pode mergulhar com ele.",
        "A caixa tem 5 mm de espessura.",
        "É réplica do Submariner.",
    ],
)
def test_gate_still_blocks_what_differs(watch_facts, text):
    assert hard_review_customer_reply(text, product_collection="watches")


def test_watch_facts_do_not_cover_jewelry(watch_facts):
    assert hard_review_customer_reply(
        "Tem 41 mm de diâmetro.", product_collection="feminine"
    )


# -- o atendimento responde com a ficha -----------------------------------------------


class _Adapter:
    channel = "whatsapp"
    api_status = "SIMULATED"

    def __init__(self):
        self.limits = {"max_text": 4096}
        self.sent = []

    def window_open(self, conversation_id, now):
        return True

    def send(self, conversation_id, text, *, idempotency_key, human_written=False):
        self.sent.append(text)
        return {"status": "SENT", "message_id": str(len(self.sent))}


def engine(tmp_path):
    alerts = []
    adapter = _Adapter()
    store = ConversationStore(
        tmp_path / "cv.sqlite3", encryption_key=Fernet.generate_key().decode()
    )
    return (
        ConversationEngine(
            store,
            adapters={"whatsapp": adapter},
            alert_sender=lambda alert: alerts.append(alert) or "TEST",
            mode_resolver=lambda _channel: "AUTO",
            knowledge_context=dict,
        ),
        adapter,
        alerts,
    )


def test_agent_answers_with_the_sheet_when_confirmed(tmp_path, watch_facts):
    conversations, adapter, alerts = engine(tmp_path)

    result = conversations.handle(
        InboundText("whatsapp", "c1", "m1", "qual o tamanho do Ocean Blue?")
    )

    assert result["action"] == "SEND"
    assert result["intent"] == "fato_confirmado"
    assert "41 mm" in adapter.sent[-1]
    assert alerts == []


def test_agent_still_asks_the_team_while_pending(tmp_path):
    conversations, adapter, alerts = engine(tmp_path)

    result = conversations.handle(
        InboundText("whatsapp", "c1", "m1", "qual o tamanho do Ocean Blue?")
    )

    assert result["intent"] == "fato_pendente"
    assert "equipe confirma" in adapter.sent[-1]
    assert alerts[-1]["reason"] == "FATO_NAO_CONFIRMADO"


# -- comando e rota -------------------------------------------------------------------


def test_cli_status_check_and_error(tmp_path, monkeypatch, capsys):
    assert watch_sheet.main(["status"]) == 0
    assert "Como liberar" in capsys.readouterr().out
    assert watch_sheet.main(["check"]) == 0
    monkeypatch.setenv("WATCH_SHEET_PATH", str(write(tmp_path, "schema: outro\n")))
    assert watch_sheet.main(["check"]) == 2
    assert "Ficha com erro" in capsys.readouterr().out


def test_readiness_route(tmp_path):
    from integrations.webhook import app, rate_store

    rate_store.clear()
    with patch.dict(
        "os.environ",
        {
            "VERATUS_ADMIN_TOKEN": "ficha-admin",
            "VERATUS_AGENT_RUNTIME_DIR": str(tmp_path),
        },
    ):
        client = app.test_client()
        response = client.get(
            "/os/watches/readiness", headers={"X-Veratus-Admin-Token": "ficha-admin"}
        )
        denied = client.get("/os/watches/readiness")

    assert response.status_code == 200
    assert len(response.json["models"]) == 9
    assert denied.status_code == 401
