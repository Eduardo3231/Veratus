"""Achados da revisão do fluxo contínuo: dados pessoais, chamadas ao fundador,
marketplace, concorrência e aviso de pagamento."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from integrations import webhook
from veratus_agents import conversations
from veratus_agents.conversations import (
    OFFER_SENTENCE,
    ConversationEngine,
    ConversationStore,
    InboundText,
    TemplateSalesModel,
    asks_for_person,
    classify,
    summarize,
)
from veratus_agents.policy import hard_review_customer_reply

KEY = Fernet.generate_key().decode("ascii")
# Documentos fictícios montados em tempo de execução: nenhum número com forma de
# CPF fica versionado (tests/test_seller_identity.py).


def _cpf(digits):
    return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"


CPF_A = _cpf("12345678909")
CPF_B = _cpf("98765432100")


class _Adapter:
    api_status = "SIMULATED"

    def __init__(self, inbound=()):
        self.limits = {"max_text": 4096}
        self.inbound = list(inbound)
        self.marks = {}
        self.sent = []

    def window_open(self, conversation_id, now):
        return True

    def send(self, conversation_id, text, *, idempotency_key, human_written=False):
        self.sent.append(text)
        return {"status": "SENT", "message_id": f"m{len(self.sent)}"}

    def pending(self, limit):
        return self.inbound[:limit]

    def mark(self, message_id, status):
        self.marks[message_id] = status


class _LiveModel:
    """Modelo livre de teste: aprova o próprio texto, como o revisor real faria."""

    name = "sales-v2-live"

    def __init__(self, reply="Claro! Posso te mostrar os relógios mais pedidos?"):
        self.reply = reply
        self.calls = 0

    def draft(self, request):
        self.calls += 1
        return {
            "customer_reply": self.reply,
            "commercial_claims": [],
            "review": {"approved_for_human_review": True},
        }

    def rewrite(self, request, draft, issues):
        return draft


def _engine(tmp_path, channel="whatsapp", model=None, adapter=None):
    store = ConversationStore(tmp_path / "cv.sqlite3", encryption_key=KEY)
    adapter = adapter or _Adapter()
    pings = []
    engine = ConversationEngine(
        store,
        adapters={channel: adapter},
        model=model or TemplateSalesModel(),
        alert_sender=lambda alert: pings.append(alert) or "TEST",
        mode_resolver=lambda _channel: "AUTO",
        knowledge_context=dict,
    )
    return engine, adapter, pings


def _say(engine, text, channel="whatsapp", conversation="c1", n=[0]):  # noqa: B006
    n[0] += 1
    return engine.handle(InboundText(channel, conversation, f"r{n[0]}", text))


# -- chamada ao fundador: só quando o cliente pede -----------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Não quero falar com atendente, só quero saber o preço do Royal Blue",
        "Esse relógio tem alguma ligação com a Rolex?",
        "Quero dar de presente pro meu gerente, quanto custa?",
        "O atendente da loja me indicou o Black GMT",
        "não precisa chamar o responsável, obrigado",
    ],
)
def test_word_alone_is_not_a_person_request(text):
    assert not asks_for_person(text)
    assert classify(text, {}).trigger is None


@pytest.mark.parametrize(
    "text",
    [
        "quero falar com um atendente",
        "Prefiro falar com um atendente",
        "posso falar com o responsável?",
        "tem algum humano aí?",
        "me passa para um atendente",
        "atendente, por favor",
        "me liga?",
        "dá pra fazer uma videochamada?",
        "não sei, quero falar com uma pessoa",
    ],
)
def test_request_phrase_is_a_person_request(text):
    assert asks_for_person(text)
    assert classify(text, {}).trigger == "PEDIDO_DE_ATENDENTE"


def test_threat_with_person_request_lowers_the_channel(tmp_path):
    engine, _, pings = _engine(tmp_path)
    result = engine.person_requested(
        InboundText(
            "whatsapp", "c1", "w1", "quero falar com o dono senão vou no Procon"
        )
    )
    assert (result["action"], result["reason"]) == ("HANDOFF", "RECLAMACAO")
    assert engine.mode("whatsapp") == "AUTO_SAFE"
    # Uma chamada (a ameaça); o disjuntor fica no painel.
    assert [ping["reason"] for ping in pings] == ["RECLAMACAO"]


# -- dados pessoais fora de alertas e tarefas -----------------------------------------


def test_alerts_and_tasks_never_carry_the_customer_text(tmp_path):
    engine, _, pings = _engine(tmp_path)
    _say(engine, f"Preciso de nota fiscal no CPF {CPF_A}, pode emitir?")
    _say(
        engine,
        f"Meu nome é João Pereira, CPF {CPF_B}. A caixa é de aço?",
        conversation="c2",
    )
    engine.person_requested(
        InboundText(
            "whatsapp",
            "c3",
            "w3",
            "Quero falar com um atendente. Sou Maria Souza, CPF 12345678909, "
            "Rua das Flores 100, CEP 01310-100",
        )
    )
    text = repr(engine.store.alerts()) + repr(pings)
    for private in ("123.456", "987.654", "12345678909", "João", "Maria", "Flores"):
        assert private not in text
    reasons = {alert["reason"] for alert in engine.store.alerts()}
    assert {"SO_PESSOA", "PEDIDO_DE_ATENDENTE"} <= reasons


def test_summarize_masks_documents_and_cep():
    text = summarize(f"CPF {CPF_A}, CNPJ 12.345.678/0001-90, CEP 01310-100")
    assert "123.456" not in text and "12.345.678" not in text and "01310" not in text


# -- marketplace: só texto fixo, e o gate pega as variações ---------------------------


def test_marketplace_never_uses_the_free_model(tmp_path):
    model = _LiveModel("A entrega é em 7 dias, com frete grátis, e sai por 289,90.")
    engine, adapter, _ = _engine(tmp_path, channel="mercado_livre", model=model)
    for text in ("qual o prazo?", "quanto custa?", "aceita cartão?", "oi"):
        result = _say(engine, text, channel="mercado_livre", conversation="q-1")
        assert result["action"] == "SEND", (text, result)
    assert model.calls == 0
    assert not any("7 dias" in reply or "289" in reply for reply in adapter.sent)


@pytest.mark.parametrize(
    "text",
    [
        "Entrega em 7 dias para todo o Brasil.",
        "A entrega leva 7 dias.",
        "O relógio sai por 289,90 reais.",
        "Pague pelo MercadoPago.",
        "Te mando o link para pagamento.",
    ],
)
def test_marketplace_gate_blocks_direct_sale_terms_in_any_form(text):
    assert "condição da venda direta em marketplace" in hard_review_customer_reply(
        text, channel="mercado_livre"
    )


def test_jewelry_needs_the_word_tom():
    assert hard_review_customer_reply(
        "Colar dourado com pedra verde.", product_collection="feminine"
    )
    assert not hard_review_customer_reply(
        "Colar em tom dourado com pedra verde.", product_collection="feminine"
    )


def test_marketplace_discount_insist_really_offers_the_person(tmp_path):
    engine, adapter, _ = _engine(tmp_path, channel="shopee")
    _say(engine, "Tem desconto no Black GMT?", channel="shopee", conversation="s1")
    _say(engine, "Faz por 250?", channel="shopee", conversation="s1")
    assert OFFER_SENTENCE in adapter.sent[-1]
    accepted = _say(engine, "Sim", channel="shopee", conversation="s1")
    assert (accepted["action"], accepted["reason"]) == (
        "HANDOFF",
        "PEDIDO_DE_ATENDENTE",
    )


def test_pending_fact_reply_passes_the_gate_and_keeps_the_channel(tmp_path):
    for channel in ("whatsapp", "shopee"):
        engine, adapter, _ = _engine(tmp_path / channel, channel=channel)
        for text in ("é à prova d'água?", "qual o tamanho do relógio?"):
            assert _say(engine, text, channel=channel)["action"] == "SEND"
        assert engine.store.events(event="GATE_FAILED") == []
        assert engine.mode(channel) == "AUTO"
        assert all("resistência" not in reply for reply in adapter.sent)


# -- concorrência e mensagens sem desfecho -------------------------------------------


def test_busy_conversation_keeps_the_message_pending(tmp_path):
    item = InboundText("whatsapp", "c1", "m1", "oi")
    adapter = _Adapter([item])
    engine, _, _ = _engine(tmp_path, adapter=adapter)
    holder = engine.store.lease("whatsapp:c1")

    assert webhook._process_channel(engine, "whatsapp") == [
        {"message_id": "m1", "action": "BUSY"}
    ]
    assert adapter.marks == {} and adapter.sent == []
    engine.store.release("whatsapp:c1", holder)
    assert [r["action"] for r in webhook._process_channel(engine, "whatsapp")] == [
        "SEND"
    ]
    assert adapter.marks == {"m1": "V2_SEND"}


def test_claim_without_outcome_becomes_a_task_after_a_while(tmp_path):
    item = InboundText("whatsapp", "c1", "m1", "oi")
    adapter = _Adapter([item])
    engine, _, _ = _engine(tmp_path, adapter=adapter)
    long_ago = conversations._now() - timedelta(minutes=11)
    with patch.object(conversations, "_now", return_value=long_ago):
        engine.store.claim_message("whatsapp", "m1")  # worker caiu depois disto

    [result] = webhook._process_channel(engine, "whatsapp")
    assert result["action"] == "ERROR"
    assert adapter.marks == {"m1": "V2_ERROR"} and adapter.sent == []
    tasks = [a for a in engine.store.alerts() if a["delivery"] == "PAINEL"]
    assert [task["reason"] for task in tasks] == ["ERRO_INTERNO"]


def test_recent_claim_without_outcome_waits(tmp_path):
    engine, _, _ = _engine(tmp_path)
    engine.store.claim_message("whatsapp", "m1")
    result = engine.handle(InboundText("whatsapp", "c1", "m1", "oi"))
    assert result["action"] == "BUSY"
    finished = engine.handle(InboundText("whatsapp", "c1", "m2", "oi"))
    assert finished["action"] == "SEND"
    assert engine.handle(InboundText("whatsapp", "c1", "m2", "oi"))["action"] == (
        "DUPLICATE"
    )


def test_founder_takeover_during_the_draft_blocks_the_send(tmp_path):
    engine, adapter, _ = _engine(tmp_path)

    class _SlowModel(_LiveModel):
        def draft(self, request):
            # O fundador assume enquanto o modelo escreve.
            engine.set_state("whatsapp", "c1", "HUMAN", actor="fundador")
            return super().draft(request)

    engine.model = _SlowModel()
    result = _say(engine, "vocês entregam em Manaus de barco?")
    assert result["action"] == "QUEUE" and result["reason"] == "STATE_HUMAN"
    assert adapter.sent == []


def test_back_to_bot_forgets_the_old_offer(tmp_path):
    engine, _, pings = _engine(tmp_path)
    _say(engine, "faz um desconto?")
    _say(engine, "me dá um desconto, vai")  # desconto insistente: oferta feita
    engine.set_state("whatsapp", "c1", "HUMAN", actor="fundador")
    engine.set_state("whatsapp", "c1", "BOT", actor="fundador")
    assert _say(engine, "sim")["action"] == "SEND"
    assert pings == []


def test_yes_after_a_free_text_reply_is_not_the_catalog(tmp_path):
    engine, _, _ = _engine(tmp_path, model=_LiveModel())
    _say(engine, "vocês entregam em Manaus de barco?")
    assert engine.store.get("whatsapp", "c1")["last_intent"] == "outro_livre"
    conversation = engine.store.get("whatsapp", "c1")
    assert classify("sim", conversation).intent != "catalogo"


# -- aviso de pagamento --------------------------------------------------------------


class _Engine:
    def __init__(self, fail=False):
        self.fail = fail
        self.orders = []

    def payment_confirmed(self, order_id):
        self.orders.append(order_id)
        if self.fail:
            raise RuntimeError("banco fora")
        return []


def _payment_webhook(status, engine):
    with (
        patch("veratus_agents.payments.verify_signature", return_value=True),
        patch(
            "veratus_agents.payments.MercadoPagoClient.from_env",
            return_value=object(),
        ),
        patch(
            "veratus_agents.payments.confirm_payment",
            return_value={"status": status, "order_id": "ord-1"},
        ),
        patch.object(webhook, "_order_ledger", return_value=None),
        patch.object(webhook, "_engine_or_error", return_value=(engine, None)),
    ):
        return webhook.app.test_client().post(
            "/integrations/mercado-pago/webhook?data.id=1&type=payment", json={}
        )


def test_payment_notice_also_runs_when_the_order_was_already_paid():
    engine = _Engine()
    assert _payment_webhook("ALREADY_PROCESSED", engine).status_code == 200
    assert engine.orders == ["ord-1"]


def test_payment_notice_failure_asks_the_provider_to_retry():
    response = _payment_webhook("PAID", _Engine(fail=True))
    assert response.status_code == 500
