"""Método Harvey no agente de vendas: objeções com uma tentativa, sem pressão."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from veratus_agents.catalog import find_product
from veratus_agents.conversations import (
    OFFER_SENTENCE,
    ConversationEngine,
    ConversationStore,
    DraftRequest,
    InboundText,
    TemplateSalesModel,
)
from veratus_agents.knowledge import default_knowledge
from veratus_agents.policy import hard_review_customer_reply
from veratus_agents.workflow import SALES_INSTRUCTIONS

HARVEY = (
    "objecao_preco",
    "hesitacao",
    "hesitacao_final",
    "consultar_alguem",
    "sem_interesse",
    "pergunta_ia",
)


def test_skills_reach_the_sales_model_whole():
    skills = default_knowledge().context()["skills"]
    harvey = {k: v for k, v in skills.items() if k.startswith("harvey-")}
    assert set(harvey) == {
        "harvey-metodo",
        "harvey-objecoes",
        "harvey-fechamento",
        "harvey-limites",
        "harvey-canais",
    }
    # LiveSalesModel corta cada skill em 800 caracteres.
    assert all(len(text) <= 800 for text in harvey.values())
    assert "método Harvey" in SALES_INSTRUCTIONS


@pytest.mark.parametrize("channel", ["facebook", "whatsapp", "mercado_livre"])
@pytest.mark.parametrize("product_id", ["ocean-blue", "lumiere", None])
def test_every_harvey_reply_passes_the_gate(channel, product_id):
    product = find_product(product_id) if product_id else None
    for intent in HARVEY:
        draft = TemplateSalesModel().draft(
            DraftRequest(channel, "c", "x", intent, None, product, {})
        )
        assert not hard_review_customer_reply(
            draft["customer_reply"],
            draft["commercial_claims"],
            product_collection=(product or {}).get("collection"),
            channel=channel,
        ), (channel, product_id, intent)


def _engine(tmp_path):
    pings = []
    engine = ConversationEngine(
        ConversationStore(
            tmp_path / "cv.sqlite3", encryption_key=Fernet.generate_key().decode()
        ),
        adapters={},
        model=TemplateSalesModel(),
        alert_sender=lambda alert: pings.append(alert) or "TEST",
        mode_resolver=lambda _channel: "SHADOW",
        knowledge_context=dict,
    )
    return engine, pings


def _intents(engine, *texts):
    return [
        engine.handle(InboundText("facebook", "fb_1", f"m{i}", text))["intent"]
        for i, text in enumerate(texts)
    ]


def test_one_try_per_objection_and_no_ping(tmp_path):
    engine, pings = _engine(tmp_path)
    assert _intents(
        engine, "achei caro", "tá caro mesmo", "vou pensar", "vou pensar"
    ) == ["objecao_preco", "desconto_insistente", "hesitacao", "hesitacao_final"]
    assert pings == [] and engine.store.alerts() == []


def test_the_assistant_never_poses_as_a_person(tmp_path):
    engine, _ = _engine(tmp_path)
    first = engine.handle(InboundText("facebook", "fb_1", "m1", "você é um robô?"))
    assert first["intent"] == "pergunta_ia"
    assert "assistente virtual" in first["reply"] and OFFER_SENTENCE in first["reply"]
    accepted = engine.handle(InboundText("facebook", "fb_1", "m2", "sim"))
    assert (accepted["action"], accepted["reason"]) == (
        "HANDOFF",
        "PEDIDO_DE_ATENDENTE",
    )


# -- Harvey nos canais do kit de perfil (branding/profile-kit.md) -------------------


class _Inbox:
    def __init__(self):
        self.sent = []

    api_status = "SIMULATED"

    def window_open(self, conversation_id, now):
        return True

    def send(self, conversation_id, text, *, idempotency_key, human_written=False):
        self.sent.append(text)
        return {"status": "SENT", "message_id": f"m{len(self.sent)}"}


def _auto(tmp_path, channel, model=None):
    from veratus_agents.harvey import INTRO

    adapter = _Inbox()
    adapter.limits = {"max_text": 1000}
    engine = ConversationEngine(
        ConversationStore(
            tmp_path / "cv.sqlite3", encryption_key=Fernet.generate_key().decode()
        ),
        adapters={channel: adapter},
        model=model or TemplateSalesModel(),
        alert_sender=lambda alert: "TEST",
        mode_resolver=lambda _channel: "AUTO",
        knowledge_context=dict,
    )
    return engine, adapter, INTRO


def test_harvey_introduces_himself_once_and_never_on_marketplaces(tmp_path):
    for channel in ("whatsapp", "facebook", "instagram"):
        engine, adapter, intro = _auto(tmp_path / channel, channel)
        engine.handle(InboundText(channel, "c1", "m1", "oi"))
        engine.handle(InboundText(channel, "c1", "m2", "quais modelos vocês têm?"))
        assert adapter.sent[0].startswith(intro) and intro not in adapter.sent[1]
        assert not hard_review_customer_reply(adapter.sent[0], channel=channel)
    engine, adapter, intro = _auto(tmp_path / "ml", "mercado_livre")
    engine.handle(InboundText("mercado_livre", "q-1", "q-1", "oi"))
    assert intro not in adapter.sent[0]
    engine, adapter, intro = _auto(tmp_path / "ia", "whatsapp")
    engine.handle(InboundText("whatsapp", "c1", "m1", "você é um robô?"))
    assert adapter.sent[0].count("Harvey") == 1


def test_instagram_takes_price_and_order_to_whatsapp_with_product_and_origin(
    tmp_path,
):
    from urllib.parse import parse_qs, urlsplit

    from veratus_agents.whatsapp import extract_attribution

    class _Live:
        name = "sales-v2-live"
        calls = 0

        def draft(self, request):
            _Live.calls += 1
            return {"customer_reply": "Me passa seu endereço por aqui."}

        def rewrite(self, request, draft, issues):
            return draft

    engine, adapter, _ = _auto(tmp_path, "instagram", model=_Live())
    price = engine.handle(
        InboundText("instagram", "ig_1", "m1", "quanto custa o Ocean Blue?")
    )
    order = engine.handle(InboundText("instagram", "ig_1", "m2", "quero comprar"))
    assert (price["action"], order["intent"]) == ("SEND", "fechamento")
    assert _Live.calls == 0  # o próximo passo do kit de perfil é texto fixo
    assert "R$ 289,90" in adapter.sent[0] and "wa.me/" in adapter.sent[1]
    assert all(len(text) <= 1000 for text in adapter.sent)
    # Nada de pedir endereço no Direct: o fechamento é no WhatsApp.
    assert engine.store.get("instagram", "ig_1")["awaiting_data"] == 0
    link = adapter.sent[1].split(": ", 1)[1]
    text = parse_qs(urlsplit(link).query)["text"][0]
    attribution = extract_attribution(text)
    assert attribution["product_id"] == "ocean-blue"
    assert attribution["utm"] == {
        "utm_source": "instagram",
        "utm_medium": "direct",
        "utm_campaign": "harvey",
    }
    led = engine.store.events(event="WHATSAPP_NEXT_STEP")
    assert [e["result"] for e in led] == ["SEND", "SEND"]


def test_story_reply_is_marked_and_its_link_says_stories(tmp_path):
    from veratus_agents.channel_adapters import (
        DirectInbox,
        InstagramDirectAdapter,
        parse_instagram_messages,
    )

    own = {"id": "17800000000000000"}
    payload = {
        "object": "instagram",
        "entry": [
            {
                "messaging": [
                    {
                        "sender": {"id": "IGSID-9"},
                        "recipient": own,
                        "timestamp": 1760000000000,
                        "message": {
                            "mid": "mid-story-1",
                            "text": "quanto custa o Ocean Blue?",
                            "reply_to": {"story": {"id": "story-1"}},
                        },
                    }
                ]
            }
        ],
    }
    assert parse_instagram_messages(payload)[0]["origin"] == "stories"
    inbox = DirectInbox(
        tmp_path / "dm.sqlite3", salt="s", encryption_key=Fernet.generate_key().decode()
    )
    adapter = InstagramDirectAdapter(
        inbox, access_token="t", ig_user_id="u", enabled=True
    )
    assert adapter.receive(payload) == 1
    [item] = adapter.pending()
    assert item.origin == {"source": "instagram", "medium": "stories"}
    engine, sent, _ = _auto(tmp_path, "instagram")
    engine.handle(item)
    assert "utm_medium%3Dstories" in sent.sent[0]


def test_harvey_number_is_the_site_number():
    from pathlib import Path

    from scripts.build_landing import PHONE
    from veratus_agents.harvey import WHATSAPP_NUMBER

    site = (Path(__file__).resolve().parents[1] / "landing" / "site.js").read_text()
    assert WHATSAPP_NUMBER == PHONE and f"'{WHATSAPP_NUMBER}'" in site


def test_report_counts_channels_handoffs_to_whatsapp_and_objections(tmp_path):
    from veratus_agents.harvey import report

    engine, _, _ = _auto(tmp_path, "instagram")
    engine.handle(InboundText("instagram", "ig_1", "m1", "achei caro"))
    engine.handle(InboundText("instagram", "ig_1", "m2", "quanto custa o Ocean Blue?"))
    result = report(engine)
    assert result["agente"] == "Harvey"
    assert [c["canal"] for c in result["canais_do_perfil"]] == [
        "instagram",
        "instagram_stories",
        "whatsapp",
        "tiktok",
    ]
    assert result["conversas_por_canal"]["instagram"]["conversas"] == 1
    assert result["instagram_para_whatsapp"]["levados"] == 1
    assert [o["intencao"] for o in result["objecoes"]] == ["objecao_preco"]


def test_attribution_lines_are_not_the_question():
    from urllib.parse import parse_qs, urlsplit

    from veratus_agents.conversations import classify
    from veratus_agents.harvey import whatsapp_link

    site = (
        "Olá! Vim pelo site da Veratus e tenho interesse em Royal Blue.\n"
        "Gostaria de confirmar disponibilidade e valor.\n"
        "Referência da visita: VT-RJ5MO2 | produto=royal-blue\n"
        "Origem da visita: utm_source=instagram | utm_medium=paid"
    )
    # Antes, "Origem da visita" virava fato pendente sobre a origem do envio.
    assert classify(site, {}).intent == "preco"
    link = whatsapp_link(find_product("royal-blue"), "stories")
    harvey = parse_qs(urlsplit(link).query)["text"][0]
    assert classify(harvey, {}).intent == "fechamento"
    assert classify("qual a origem do relógio?", {}).topic == "a origem do envio"
