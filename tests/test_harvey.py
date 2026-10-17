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
