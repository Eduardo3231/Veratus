"""Atendimento autônomo (Fase 3): níveis, gatilhos, disjuntor, pagamento e canais."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from veratus_agents.channel_adapters import (
    DirectInbox,
    FounderAlertSender,
    InstagramDirectAdapter,
    ManualPasteAdapter,
    MercadoLivreConversationAdapter,
    WhatsAppAdapter,
    adapter_contract,
    marketing_allowed,
    parse_instagram_messages,
)
from veratus_agents.conversations import (
    OFFER_MARK,
    OFFER_TEXT,
    PAID_NOTICE,
    POLICY_INTENTS,
    ConversationEngine,
    ConversationStore,
    DraftRequest,
    InboundText,
    TemplateSalesModel,
    asks_for_person,
    classify,
    decide,
)
from veratus_agents.orders import OrderLedger, OrderStatus
from veratus_agents.payments import (
    MercadoPagoClient,
    PaymentLinkRequest,
    confirm_payment,
    create_payment_link,
    verify_signature,
)
from veratus_agents.policy import hard_review_customer_reply
from veratus_agents.whatsapp import WhatsAppChannel, WhatsAppCloudClient, wants_human

KEY = Fernet.generate_key().decode("ascii")
NUMBER = "5511999990000"


class FakeResponse:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status
        self.ok = status < 400

    def json(self):
        return self._data


class FakeSession:
    """Cliente HTTP falso: registra e responde; nada sai da máquina."""

    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        for fragment, data in self.responses.items():
            if fragment in url:
                return FakeResponse(data)
        return FakeResponse({"messages": [{"id": f"wamid.{len(self.calls)}"}]})

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        for fragment, data in self.responses.items():
            if fragment in url:
                return FakeResponse(data)
        return FakeResponse({})


def wa_payload(message_id, body, at=None):
    at = at or datetime.now(timezone.utc)
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messages": [
                                {
                                    "from": NUMBER,
                                    "id": message_id,
                                    "timestamp": str(int(at.timestamp())),
                                    "type": "text",
                                    "text": {"body": body},
                                }
                            ]
                        },
                    }
                ]
            }
        ],
    }


class RecordingAdapter:
    api_status = "SIMULATED"

    def __init__(self, channel="whatsapp", status="SENT"):
        self.limits = {"max_text": 4096}
        self.channel = channel
        self.status = status
        self.sent = []

    def window_open(self, conversation_id, now):
        return True

    def send(self, conversation_id, text, *, idempotency_key, human_written=False):
        self.sent.append(text)
        if self.status != "SENT":
            return {"status": self.status, "reasons": ["TESTE"]}
        return {"status": "SENT", "message_id": f"m{len(self.sent)}"}


def make_engine(tmp_path, mode="AUTO", channel="whatsapp", **kwargs):
    store = ConversationStore(tmp_path / "cv.sqlite3", encryption_key=KEY)
    adapter = kwargs.pop("adapter", None) or RecordingAdapter(channel)
    alerts = []
    engine = ConversationEngine(
        store,
        adapters={channel: adapter},
        model=kwargs.pop("model", None) or TemplateSalesModel(),
        alert_sender=lambda alert: alerts.append(alert) or "TEST",
        mode_resolver=lambda _channel: mode,
        knowledge_context=dict,
        **kwargs,
    )
    return engine, adapter, alerts


def say(engine, text, conversation="c1", channel="whatsapp", n=[0]):  # noqa: B006
    n[0] += 1
    return engine.handle(
        InboundText(
            channel=channel,
            conversation_id=conversation,
            message_id=f"msg-{n[0]}",
            text=text,
        )
    )


# -- níveis -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "risk", "expected"),
    [
        ("SHADOW", 0, "QUEUE"),
        ("SUPERVISED", 0, "QUEUE"),
        ("AUTO_SAFE", 0, "SEND"),
        ("AUTO_SAFE", 1, "QUEUE"),
        ("AUTO", 1, "SEND"),
        ("AUTO", 2, "QUEUE"),
        ("AUTO", 9, "QUEUE"),
    ],
)
def test_levels_decide_what_leaves_alone(mode, risk, expected):
    assert decide(mode, risk) == expected


def test_shadow_only_drafts(tmp_path):
    engine, adapter, _ = make_engine(tmp_path, mode="SHADOW")
    result = say(engine, "oi")

    assert result["action"] == "QUEUE"
    assert adapter.sent == []
    assert engine.store.queue()[0]["status"] == "SHADOW_DRAFT"


def test_supervised_sends_only_after_founder_approval(tmp_path):
    engine, adapter, _ = make_engine(tmp_path, mode="SUPERVISED")
    result = say(engine, "quanto custa o Ocean Blue?")
    assert result["action"] == "QUEUE" and adapter.sent == []

    approved = engine.approve_draft(result["draft_id"], actor="fundador")

    assert approved["status"] == "SENT"
    assert "289,90" in adapter.sent[0]
    assert engine.metrics()["whatsapp"]["approved_without_edit"] == 1


def test_founder_edit_goes_through_the_gate(tmp_path):
    engine, adapter, _ = make_engine(tmp_path, mode="SUPERVISED")
    draft = say(engine, "oi")["draft_id"]

    blocked = engine.approve_draft(draft, actor="fundador", text="É réplica AAA!")

    assert blocked["status"] == "BLOCKED"
    assert adapter.sent == []


def test_auto_safe_sends_risk_zero_and_queues_closing(tmp_path):
    engine, adapter, _ = make_engine(tmp_path, mode="AUTO_SAFE")

    assert say(engine, "oi")["action"] == "SEND"
    assert say(engine, "qual o prazo de entrega?")["action"] == "SEND"
    closing = say(engine, "quero comprar o Ocean Blue")

    assert closing["action"] == "QUEUE"
    assert closing["intent"] == "fechamento"
    assert len(adapter.sent) == 2


def test_blocked_send_falls_back_to_the_queue(tmp_path):
    adapter = RecordingAdapter(status="BLOCKED")
    engine, _, _ = make_engine(tmp_path, mode="AUTO", adapter=adapter)
    result = say(engine, "oi")

    assert result["action"] == "QUEUE"
    assert result["external_message_sent"] is False
    assert engine.store.queue()[0]["reason"] == "TESTE"


# -- do "oi" ao link de pagamento ---------------------------------------------------


def test_whatsapp_conversation_reaches_the_payment_link_in_auto(tmp_path):
    wa = WhatsAppChannel(tmp_path / "wa.sqlite3", salt="sal", encryption_key=KEY)
    wa_session = FakeSession()
    client = WhatsAppCloudClient("token", "PNID", session=wa_session)
    adapter = WhatsAppAdapter(wa, client=client, enabled=True)
    ledger = OrderLedger(tmp_path / "orders.sqlite3")
    mp_session = FakeSession(
        {
            "checkout/preferences": {
                "id": "pref-1",
                "init_point": "https://mp.test/p/1",
            },
            "/v1/payments/77": {
                "id": 77,
                "status": "approved",
                "external_reference": "ord-test-0001",
                "currency_id": "BRL",
                "transaction_amount": 289.90,
            },
        }
    )
    mp = MercadoPagoClient("mp-token", session=mp_session)

    def order_factory(channel, conversation_id, product):
        from veratus_agents.orders import OrderChannel, OrderRecord, PaymentMethod

        return ledger.record(
            OrderRecord(
                order_id="ord-test-0001",
                sku=product["id"],
                channel=OrderChannel(channel),
                conversation_ref=conversation_id,
                sale_price=Decimal("289.90"),
                payment_method=PaymentMethod.UNKNOWN,
                evidence_ref="conversation:whatsapp",
                recorded_by="sales-agent",
            )
        )[1]

    def payment_linker(order, product):
        return create_payment_link(
            PaymentLinkRequest(
                order_id=order["order_id"],
                title=product["name"],
                amount=Decimal(order["sale_price"]),
                sku=order["sku"],
            ),
            client=mp,
            enabled=True,
        )

    engine, _, _ = make_engine(
        tmp_path,
        mode="AUTO",
        adapter=adapter,
        order_factory=order_factory,
        payment_linker=payment_linker,
        order_lookup=ledger.get,
    )
    actions = []
    for index, text in enumerate(
        [
            "oi",
            "quanto custa o Ocean Blue?",
            "quero comprar o Ocean Blue",
            "Maria Souza, CEP 01310-100, Av. Paulista 1000 ap 12",
        ]
    ):
        wa.receive(wa_payload(f"wamid.in.{index}", text))
        for inbound in adapter.pending():
            result = engine.handle(inbound)
            adapter.mark(inbound.message_id, result["action"])
            actions.append((result["action"], result.get("intent")))

    assert actions == [
        ("SEND", "saudacao"),
        ("SEND", "preco"),
        ("SEND", "fechamento"),
        ("SEND", "dados_pedido"),
    ]
    sent = [
        call[2]["json"]["text"]["body"]
        for call in wa_session.calls
        if call[0] == "POST" and "messages" in call[1]
    ]
    assert "https://mp.test/p/1" in sent[-1]
    preference = mp_session.calls[0][2]["json"]
    assert preference["external_reference"] == "ord-test-0001"
    assert ledger.get("ord-test-0001")["status"] == "PENDING"
    # Dados do cliente: cifrados e fora da auditoria.
    conversation = engine.store.conversations()[0]
    stored = engine.store.customer_data(conversation["conv_key"])
    assert "01310-100" in stored
    events = json.dumps(engine.store.events())
    assert "01310" not in events and "Paulista" not in events
    inbox = json.dumps(wa.get(conversation["conversation_id"]), default=str)
    assert "01310" not in inbox and "Paulista" not in inbox

    # Conclusão da venda: só o provedor confirma, e o cliente é avisado uma vez.
    confirmed = confirm_payment("77", client=mp, ledger=ledger)
    notices = engine.payment_confirmed(confirmed["order_id"])
    replay = engine.payment_confirmed(confirmed["order_id"])
    sent = [
        call[2]["json"]["text"]["body"]
        for call in wa_session.calls
        if call[0] == "POST" and "messages" in call[1]
    ]
    assert confirmed["status"] == "PAID"
    assert [item["action"] for item in notices] == ["SEND"]
    assert [item["action"] for item in replay] == ["DUPLICATE"]
    assert sent[-1] == PAID_NOTICE and sent.count(PAID_NOTICE) == 1
    assert len(engine.store.events(event="ORDER_PAID_NOTICE")) == 1


def _order_engine(tmp_path, mode, linked):
    def order_factory(channel, conversation_id, product):
        return {"order_id": "ord-lvl-0001", "sku": product["id"]}

    def payment_linker(order, product):
        linked.append(order["order_id"])
        return {"status": "CREATED", "url": "https://mp.test/p/x"}

    return make_engine(
        tmp_path, mode=mode, order_factory=order_factory, payment_linker=payment_linker
    )


@pytest.mark.parametrize(
    ("mode", "cap"),
    [
        ("SHADOW", None),
        ("SUPERVISED", None),
        ("AUTO_SAFE", None),
        ("AUTO", "AUTO_SAFE"),  # o disjuntor rebaixou o canal
    ],
)
def test_payment_link_is_only_created_at_the_allowed_level(tmp_path, mode, cap):
    linked = []
    engine, adapter, _ = _order_engine(tmp_path, mode, linked)
    if cap:
        engine.store.set_cap("whatsapp", cap, "teste")
    say(engine, "quero comprar o Ocean Blue")
    result = say(engine, "Maria Souza, CEP 01310-100, Av. Paulista 1000")

    effective = cap or mode
    assert (result["action"], result["risk"]) == ("QUEUE", 9)
    assert linked == []  # nenhuma escrita no Mercado Pago
    assert "mp.test" not in result["reply"] and adapter.sent == []
    [event] = engine.store.events(event="PAYMENT_LINK")
    assert (event["result"], event["reason"]) == (
        "BLOCKED",
        f"CHANNEL_MODE_{effective}",
    )
    assert engine.store.get("whatsapp", "c1")["order_id"] == "ord-lvl-0001"


def test_payment_link_is_created_in_auto(tmp_path):
    linked = []
    engine, adapter, _ = _order_engine(tmp_path, "AUTO", linked)
    say(engine, "quero comprar o Ocean Blue")
    result = say(engine, "Maria Souza, CEP 01310-100, Av. Paulista 1000")

    assert result["action"] == "SEND"
    assert linked == ["ord-lvl-0001"]
    assert "https://mp.test/p/x" in adapter.sent[-1]


# -- aviso de pagamento confirmado (conclusão da venda) -------------------------------


class ClosedWindowAdapter(RecordingAdapter):
    def window_open(self, conversation_id, now):
        return False


class TemplateAdapter(ClosedWindowAdapter):
    def __init__(self):
        super().__init__()
        self.templates = []

    def send_template(self, conversation_id, template, *, idempotency_key):
        self.templates.append((template, idempotency_key))
        return {"status": "SENT", "message_id": "tpl-1"}


def _paid_engine(tmp_path, mode="AUTO", adapter=None):
    engine, adapter, alerts = make_engine(tmp_path, mode=mode, adapter=adapter)
    say(engine, "oi")
    engine.store.update("whatsapp:c1", order_id="ord-paid-0001")
    return engine, adapter, alerts


@pytest.mark.parametrize("mode", ["AUTO_SAFE", "AUTO"])
def test_paid_order_is_announced_once(tmp_path, mode):
    engine, adapter, alerts = _paid_engine(tmp_path, mode)
    first = engine.payment_confirmed("ord-paid-0001")
    again = engine.payment_confirmed("ord-paid-0001")

    assert [item["action"] for item in first] == ["SEND"]
    assert [item["action"] for item in again] == ["DUPLICATE"]
    assert adapter.sent[-1] == PAID_NOTICE
    assert adapter.sent.count(PAID_NOTICE) == 1
    [notice] = engine.store.events(event="ORDER_PAID_NOTICE")
    assert notice["result"] == "SENT"
    assert engine.store.get("whatsapp", "c1")["paid"] == 1
    assert alerts == []


@pytest.mark.parametrize(
    ("mode", "status"), [("SHADOW", "SHADOW_DRAFT"), ("SUPERVISED", "PENDING_APPROVAL")]
)
def test_paid_notice_waits_in_the_queue_below_auto_safe(tmp_path, mode, status):
    engine, adapter, _ = _paid_engine(tmp_path, mode)
    [result] = engine.payment_confirmed("ord-paid-0001")

    assert result["action"] == "QUEUE"
    assert adapter.sent == []  # SHADOW nunca envia
    draft = engine.store.draft(result["draft_id"])
    assert (draft["status"], draft["reply"]) == (status, PAID_NOTICE)
    assert engine.payment_confirmed("ord-paid-0001")[0]["action"] == "DUPLICATE"
    notices = [d for d in engine.store.queue() if d["intent"] == "pagamento_confirmado"]
    assert len(notices) == 1


def test_paid_notice_outside_the_window_only_uses_the_utility_template(tmp_path):
    engine, adapter, _ = _paid_engine(tmp_path, adapter=ClosedWindowAdapter())
    [queued] = engine.payment_confirmed("ord-paid-0001")

    assert queued["action"] == "QUEUE"
    assert queued["reason"] == "CUSTOMER_SERVICE_WINDOW_CLOSED"
    assert adapter.sent == []

    templated, template_adapter, _ = _paid_engine(
        tmp_path / "t", adapter=TemplateAdapter()
    )
    [sent] = templated.payment_confirmed("ord-paid-0001")

    assert sent["action"] == "SEND"
    assert template_adapter.templates == [("pedido_confirmado", "paid:ord-paid-0001")]
    assert template_adapter.sent == []  # texto livre fora da janela, nunca
    [event] = templated.store.events(event="ORDER_PAID_NOTICE")
    assert json.loads(event["detail_json"])["via"] == "pedido_confirmado"


def test_paid_notice_goes_to_the_founder_when_he_has_the_conversation(tmp_path):
    engine, adapter, _ = _paid_engine(tmp_path)
    engine.set_state("whatsapp", "c1", "HUMAN", actor="fundador")
    [result] = engine.payment_confirmed("ord-paid-0001")

    assert (result["action"], result["reason"]) == ("QUEUE", "STATE_HUMAN")
    assert adapter.sent.count(PAID_NOTICE) == 0


class FailingAdapter(RecordingAdapter):
    def send(self, conversation_id, text, *, idempotency_key, human_written=False):
        raise ConnectionError("rede")


def test_paid_notice_failure_never_breaks_the_provider_webhook(tmp_path):
    engine, _, _ = make_engine(tmp_path, mode="AUTO", adapter=FailingAdapter())
    engine.store.conversation("whatsapp", "c1")
    engine.store.update("whatsapp:c1", order_id="ord-paid-0001")
    [result] = engine.payment_confirmed("ord-paid-0001")

    assert result["action"] == "QUEUE"
    assert result["reason"] == "SEND_ERROR_ConnectionError"
    assert engine.store.get("whatsapp", "c1")["paid"] == 1


def test_customer_data_is_not_collected_before_the_yes(tmp_path):
    engine, _, _ = make_engine(tmp_path, mode="AUTO")
    result = say(engine, "meu CEP é 01310-100")

    assert result["intent"] != "dados_pedido"
    assert engine.store.customer_data("whatsapp:c1") is None


# -- passagem ao fundador: CHAMADA, TAREFA e só a IA ----------------------------------


def panel_tasks(engine):
    return [item for item in engine.store.alerts() if item["delivery"] == "PAINEL"]


@pytest.mark.parametrize(
    ("messages", "reason"),
    [
        (["quero falar com um atendente"], "PEDIDO_DE_ATENDENTE"),
        (["posso falar com o responsável?"], "PEDIDO_DE_ATENDENTE"),
        (["me liga agora, por favor"], "PEDIDO_DE_ATENDENTE"),
        (["dá pra fazer uma videochamada?"], "PEDIDO_DE_ATENDENTE"),
        (["se não resolver, vou no Procon"], "RECLAMACAO"),
        # Ameaça e pedido de pessoa juntos: a ameaça vale e rebaixa o canal.
        (["quero falar com o responsável agora, senão vou no Procon"], "RECLAMACAO"),
        (["vou chamar meu advogado"], "RECLAMACAO"),
        # Oferta do agente aceita com um "sim" curto.
        (["hmm", "e aquilo", "sei la", "sim"], "PEDIDO_DE_ATENDENTE"),
        (
            ["faz um desconto?", "faz por 250, me dá desconto", "pode chamar"],
            "PEDIDO_DE_ATENDENTE",
        ),
        (["que absurdo, péssimo isso", "quero"], "PEDIDO_DE_ATENDENTE"),
    ],
)
def test_call_hands_over_and_alerts_the_founder(tmp_path, messages, reason):
    engine, _, alerts = make_engine(tmp_path, mode="AUTO")
    for text in messages:
        result = say(engine, text)

    assert result["action"] == "HANDOFF"
    assert result["reason"] == reason
    assert engine.store.get("whatsapp", "c1")["state"] == "HUMAN"
    [alert] = [item for item in alerts if item["reason"] == reason]
    assert len(alert["summary"].splitlines()) <= 3
    assert engine.metrics()["whatsapp"]["handoffs_by_reason"] == {reason: 1}
    # Parado: a próxima mensagem não gera resposta.
    assert say(engine, "oi")["action"] == "SKIPPED"


@pytest.mark.parametrize(
    ("text", "intent", "reason", "fragment"),
    [
        (
            "fui cobrado duas vezes no cartão",
            "pagamento_conferencia",
            "PROBLEMA_PAGAMENTO",
            "conferir o pagamento no Mercado Pago",
        ),
        (
            "o link não funciona",
            "pagamento_conferencia",
            "PROBLEMA_PAGAMENTO",
            "conferir o pagamento no Mercado Pago",
        ),
        (
            "pode emitir nota fiscal no CNPJ?",
            "so_pessoa",
            "SO_PESSOA",
            "equipe confirma",
        ),
        ("vocês vendem no atacado?", "so_pessoa", "SO_PESSOA", "equipe confirma"),
        (
            "vocês têm vaga de emprego?",
            "fora_de_vendas",
            "FORA_DE_VENDAS",
            "fora do atendimento de vendas",
        ),
        (
            "isso é um absurdo, péssimo atendimento",
            "insatisfacao",
            "RECLAMACAO",
            "Se preferir falar com o responsável da Veratus, é só me dizer.",
        ),
        # Reclame Aqui é insatisfação, não ação judicial: tarefa, sem ping.
        (
            "vou postar no Reclame Aqui se não chegar",
            "insatisfacao",
            "RECLAMACAO",
            "Se preferir falar com o responsável da Veratus, é só me dizer.",
        ),
        (
            "qual a garantia do relógio?",
            "fato_pendente",
            "FATO_NAO_CONFIRMADO",
            "equipe confirma",
        ),
    ],
)
def test_task_keeps_the_agent_and_skips_the_ping(
    tmp_path, text, intent, reason, fragment
):
    engine, adapter, alerts = make_engine(tmp_path, mode="AUTO")
    result = say(engine, text)

    assert result["action"] == "SEND"
    assert (result["intent"], result["task"]) == (intent, reason)
    assert fragment in adapter.sent[-1]
    assert alerts == []  # sem ping
    [task] = panel_tasks(engine)
    assert task["reason"] == reason
    assert len(task["summary"].splitlines()) <= 3
    assert engine.store.get("whatsapp", "c1")["state"] == "BOT"
    metrics = engine.metrics()["whatsapp"]
    assert metrics["tasks_by_reason"] == {reason: 1}
    assert metrics["handoffs_by_reason"] == {}
    # Sem ameaça jurídica, o disjuntor não rebaixa o canal.
    assert engine.mode("whatsapp") == "AUTO"
    assert say(engine, "e o frete?")["action"] == "SEND"


def test_address_change_after_payment_is_a_task(tmp_path):
    engine, adapter, alerts = make_engine(tmp_path, mode="AUTO")
    say(engine, "oi")
    engine.store.update("whatsapp:c1", paid=1)
    result = say(engine, "quero trocar o endereço de entrega")

    assert (result["intent"], result["task"]) == ("endereco_pago", "SINAL_DE_FRAUDE")
    assert "endereço não muda por aqui depois do pagamento" in adapter.sent[-1]
    assert alerts == []
    assert [task["reason"] for task in panel_tasks(engine)] == ["SINAL_DE_FRAUDE"]


def test_unpriced_product_is_a_task_not_a_loose_promise(tmp_path):
    engine, adapter, alerts = make_engine(tmp_path, mode="AUTO")
    result = say(engine, "quanto custa o Halo Verde?")

    assert (result["intent"], result["task"]) == ("preco", "FATO_NAO_CONFIRMADO")
    assert "equipe confirma" in adapter.sent[-1]
    assert alerts == []
    [task] = panel_tasks(engine)
    assert "Halo Verde" in task["suggestion"]


@pytest.mark.parametrize(
    ("messages", "intent", "fragment"),
    [
        (
            ["já paguei, segue o comprovante"],
            "pagamento_informado",
            "chega automaticamente pelo Mercado Pago",
        ),
        (
            ["manda primeiro que eu pago quando chegar"],
            "pagamento_fora_do_link",
            "depois do pagamento confirmado pelo link do Mercado Pago",
        ),
        (
            ["posso fazer um depósito?"],
            "pagamento_fora_do_link",
            "link do Mercado Pago",
        ),
        (["manda o pix pessoal"], "pagamento_fora_do_link", "link do Mercado Pago"),
        (
            ["consegue entrega expressa pra amanhã?"],
            "politica_prazo",
            "entrega em até 7 dias",
        ),
        (["manda por sedex?"], "politica_prazo", "entrega em até 7 dias"),
        (
            ["dá pra trocar depois de 30 dias?"],
            "politica_troca",
            "desistir da compra em até 7 dias após o recebimento",
        ),
        (
            ["faz um desconto?", "faz por 250, me dá desconto"],
            "desconto_insistente",
            "Se preferir falar com o responsável da Veratus, é só me dizer.",
        ),
        (
            ["faz um desconto?", "faz por 250", "e um cupom?"],
            "desconto_insistente",
            "Mantemos o preço",
        ),
    ],
)
def test_ai_only_answers_without_task_or_ping(tmp_path, messages, intent, fragment):
    engine, adapter, alerts = make_engine(tmp_path, mode="AUTO")
    for text in messages:
        result = say(engine, text)

    assert result["action"] == "SEND"
    assert result["intent"] == intent
    assert "task" not in result
    assert fragment in adapter.sent[-1]
    assert alerts == [] and engine.store.alerts() == []
    assert engine.store.get("whatsapp", "c1")["state"] == "BOT"


def test_first_discount_keeps_the_current_reply_and_the_second_holds_the_price(
    tmp_path,
):
    engine, adapter, _ = make_engine(tmp_path, mode="AUTO")
    first = say(engine, "faz um desconto?")
    second = say(engine, "me dá um desconto vai")

    assert first["intent"] == "preco"
    assert "não trabalhamos com desconto" in adapter.sent[0]
    assert second["intent"] == "desconto_insistente"
    for fact in (
        "frete grátis",
        "sem taxa adicional",
        "entrega em até 7 dias",
        "desistência em até 7 dias após o recebimento",
    ):
        assert fact in adapter.sent[1]
    assert engine.store.get("whatsapp", "c1")["last_intent"] == OFFER_MARK


def test_payment_is_never_confirmed_by_the_customer_message(tmp_path):
    engine, adapter, _ = make_engine(tmp_path, mode="AUTO")
    say(engine, "paguei! segue o print do comprovante")

    assert "confirmado" not in adapter.sent[-1]
    assert engine.store.get("whatsapp", "c1")["paid"] == 0
    assert engine.store.events(event="ORDER_PAID") == []


def test_stalled_agent_offers_the_founder_instead_of_handing_off(tmp_path):
    engine, adapter, alerts = make_engine(tmp_path, mode="AUTO")
    for text in ["hmm", "e aquilo", "sei la"]:
        result = say(engine, text)

    assert (result["action"], result["intent"]) == ("SEND", OFFER_MARK)
    assert adapter.sent[-1] == OFFER_TEXT
    assert engine.store.get("whatsapp", "c1")["last_intent"] == OFFER_MARK
    assert alerts == [] and engine.store.alerts() == []
    offers = engine.store.events(event="HANDOFF_OFFERED")
    assert [item["reason"] for item in offers] == ["IA_TRAVADA"]
    # Recusa segue normal: o agente continua e ninguém é chamado.
    declined = say(engine, "não, pode seguir")
    assert declined["action"] == "SEND"
    assert engine.store.get("whatsapp", "c1")["state"] == "BOT"
    assert alerts == []


class UnapprovedModel(TemplateSalesModel):
    def draft(self, request):
        return {
            **super().draft(request),
            "review": {"approved_for_human_review": False},
        }


def test_other_intent_guides_the_customer_and_follows_the_reviewer(tmp_path):
    engine, adapter, _ = make_engine(tmp_path, mode="AUTO")
    approved = say(engine, "hmm")

    assert (approved["action"], approved["risk"]) == ("SEND", 1)
    assert adapter.sent[-1] == (
        "Posso te ajudar a escolher um relógio ou uma joia da Veratus. "
        "Quer ver os modelos?"
    )
    assert "equipe" not in adapter.sent[-1]
    assert say(engine, "sim")["intent"] == "catalogo"

    unreviewed, _, _ = make_engine(tmp_path / "b", mode="AUTO", model=UnapprovedModel())
    refused = say(unreviewed, "hmm")
    assert (refused["action"], refused["risk"]) == ("QUEUE", 9)


def test_every_policy_reply_passes_the_gate():
    model = TemplateSalesModel()
    for intent in sorted(POLICY_INTENTS | {"outro"}):
        for channel in ("whatsapp", "mercado_livre"):
            draft = model.draft(
                DraftRequest(channel, "c1", "texto", intent, None, None, {})
            )
            assert draft["customer_reply"]
            assert (
                hard_review_customer_reply(
                    draft["customer_reply"],
                    draft["commercial_claims"],
                    channel=channel,
                )
                == []
            ), (intent, channel)


class BadModel(TemplateSalesModel):
    def draft(self, request):
        return {**super().draft(request), "customer_reply": "É réplica AAA, só hoje!"}

    def rewrite(self, request, draft, issues):
        return draft


def test_two_gate_failures_offer_the_founder_and_trip_the_breaker(tmp_path):
    engine, adapter, alerts = make_engine(tmp_path, mode="AUTO", model=BadModel())
    result = say(engine, "oi")

    assert (result["action"], result["intent"]) == ("SEND", OFFER_MARK)
    assert adapter.sent == [OFFER_TEXT]  # nunca o rascunho reprovado
    assert engine.mode("whatsapp") == "AUTO_SAFE"
    # O disjuntor fica no painel: o fundador só é chamado se o cliente pedir.
    assert alerts == []
    assert [task["reason"] for task in panel_tasks(engine)] == ["DISJUNTOR"]
    assert engine.store.get("whatsapp", "c1")["state"] == "BOT"
    accepted = say(engine, "sim, por favor")
    assert (accepted["action"], accepted["reason"]) == (
        "HANDOFF",
        "PEDIDO_DE_ATENDENTE",
    )


def test_complaint_lowers_the_channel_one_level(tmp_path):
    engine, _, _ = make_engine(tmp_path, mode="AUTO_SAFE")
    say(engine, "isso é um absurdo, vou no procon")

    assert engine.mode("whatsapp") == "SUPERVISED"


def test_founder_resolves_and_the_bot_returns(tmp_path):
    engine, _, _ = make_engine(tmp_path, mode="AUTO")
    say(engine, "quero falar com um atendente")
    engine.set_state("whatsapp", "c1", "BOT", actor="fundador")

    assert say(engine, "oi")["action"] == "SEND"


def test_duplicate_webhook_delivery_is_handled_once(tmp_path):
    engine, adapter, _ = make_engine(tmp_path, mode="AUTO")
    inbound = InboundText("whatsapp", "c1", "same-id", "oi")

    assert engine.handle(inbound)["action"] == "SEND"
    assert engine.handle(inbound)["action"] == "DUPLICATE"
    assert len(adapter.sent) == 1


def test_classifier_reads_intents():
    assert classify("oi", {}).intent == "saudacao"
    assert classify("quanto custa?", {}).intent == "preco"
    assert classify("aceita cartão?", {}).intent == "como_pedir"
    assert classify("01310-100", {"awaiting_data": 1}).intent == "dados_pedido"
    paid_address = classify("quero trocar o endereço", {"paid": 1})
    assert (paid_address.intent, paid_address.task) == (
        "endereco_pago",
        "SINAL_DE_FRAUDE",
    )
    assert classify("quero trocar o endereço", {}).task is None


def test_short_yes_accepts_only_right_after_the_offer():
    offer = {"last_intent": OFFER_MARK}
    for yes in (
        "sim",
        "Sim!",
        "quero",
        "pode",
        "pode chamar",
        "chama",
        "por favor",
        "sim, quero",
        "pode ser",
    ):
        assert classify(yes, offer).trigger == "PEDIDO_DE_ATENDENTE", yes
    for no in ("não", "não precisa", "pode seguir", "sim, mas qual o prazo?"):
        assert classify(no, offer).trigger is None, no
    # Sem a oferta, "sim, quero" e "pode ser" continuam sendo fechamento.
    assert classify("sim, quero", {}).intent == "fechamento"
    assert classify("pode ser", {"last_intent": "preco"}).intent == "fechamento"


def test_whatsapp_entry_uses_the_engine_phrase_list():
    for text in ("Prefiro falar com um atendente", "me liga?", "chamada de vídeo"):
        assert wants_human(text) and asks_for_person(text)
    assert not wants_human("qual o prazo?")


# -- disjuntor e prontidão ------------------------------------------------------------


def test_readiness_needs_fifty_clean_shadow_conversations(tmp_path):
    engine, _, _ = make_engine(tmp_path, mode="SHADOW")
    for index in range(50):
        result = say(engine, "oi", conversation=f"c{index}")
        engine.approve_draft(result["draft_id"], actor="fundador")

    report = engine.readiness()["whatsapp"]

    assert report["eligible"] is True
    assert report["next_mode"] == "SUPERVISED"
    assert engine.readiness()["instagram"]["eligible"] is False


def test_readiness_never_raises_the_level_by_itself(tmp_path):
    engine, _, _ = make_engine(tmp_path, mode="SHADOW")
    for index in range(50):
        engine.approve_draft(
            say(engine, "oi", conversation=f"c{index}")["draft_id"], actor="fundador"
        )
    engine.readiness()

    assert engine.mode("whatsapp") == "SHADOW"


# -- pagamento ----------------------------------------------------------------------


def signed(secret, data_id, request_id, ts):
    manifest = f"id:{data_id};request-id:{request_id};ts:{ts};"
    digest = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return f"ts={ts},v1={digest}"


def test_mercado_pago_signature_is_validated():
    ts = str(int(time.time()))
    header = signed("segredo", "123", "req-1", ts)

    assert verify_signature(
        signature_header=header,
        request_id="req-1",
        data_id="123",
        secret="segredo",
        now=time.time(),
    )
    assert not verify_signature(
        signature_header=header, request_id="req-1", data_id="124", secret="segredo"
    )
    assert not verify_signature(
        signature_header=header,
        request_id="req-1",
        data_id="123",
        secret="segredo",
        now=time.time() + 3600,
    )


@pytest.fixture
def pending_order(tmp_path):
    from veratus_agents.orders import OrderChannel, OrderRecord, PaymentMethod

    ledger = OrderLedger(tmp_path / "orders.sqlite3")
    ledger.record(
        OrderRecord(
            order_id="ord-pay-0001",
            sku="ocean-blue",
            channel=OrderChannel.WHATSAPP,
            sale_price=Decimal("289.90"),
            payment_method=PaymentMethod.UNKNOWN,
            evidence_ref="conversation:whatsapp",
            recorded_by="sales-agent",
        )
    )
    return ledger


def payment(**changes):
    return {
        "id": 99,
        "status": "approved",
        "external_reference": "ord-pay-0001",
        "currency_id": "BRL",
        "transaction_amount": 289.90,
        **changes,
    }


def test_payment_is_confirmed_only_by_provider_readback(pending_order):
    client = MercadoPagoClient("t", session=FakeSession({"/v1/payments/99": payment()}))
    result = confirm_payment("99", client=client, ledger=pending_order)

    assert result["status"] == "PAID"
    assert pending_order.get("ord-pay-0001")["status"] == OrderStatus.PAID.value


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"status": "pending"}, "PAYMENT_NOT_APPROVED"),
        ({"transaction_amount": 10.0}, "AMOUNT_MISMATCH"),
        ({"currency_id": "USD"}, "CURRENCY_MISMATCH"),
    ],
)
def test_payment_that_does_not_match_is_not_confirmed(pending_order, changes, reason):
    client = MercadoPagoClient(
        "t", session=FakeSession({"/v1/payments/99": payment(**changes)})
    )
    result = confirm_payment("99", client=client, ledger=pending_order)

    assert result["status"] == "NOT_CONFIRMED"
    assert reason in result["reasons"]
    assert pending_order.get("ord-pay-0001")["status"] == "PENDING"


def test_payment_link_is_blocked_while_the_flag_is_off():
    session = FakeSession()
    result = create_payment_link(
        PaymentLinkRequest(
            "ord-x-000001", "Ocean Blue", Decimal("289.90"), "ocean-blue"
        ),
        client=MercadoPagoClient("t", session=session),
        enabled=False,
    )

    assert result["status"] == "BLOCKED"
    assert "PAYMENT_LINKS_DISABLED" in result["reasons"]
    assert result["preview"]["installments_interest"] == "buyer"
    assert session.calls == []


# -- canais -------------------------------------------------------------------------


def test_every_adapter_has_the_same_contract(tmp_path):
    wa = WhatsAppChannel(tmp_path / "wa.sqlite3", salt="s", encryption_key=KEY)
    inbox = DirectInbox(tmp_path / "dm.sqlite3", salt="s", encryption_key=KEY)
    adapters = [
        WhatsAppAdapter(wa, client=None, enabled=False),
        InstagramDirectAdapter(inbox, access_token=None, ig_user_id=None),
        MercadoLivreConversationAdapter(token_provider=None, seller_id=None),
        ManualPasteAdapter("shopee"),
        ManualPasteAdapter("tiktok_shop"),
        ManualPasteAdapter("amazon"),
    ]
    for adapter in adapters:
        contract = adapter_contract(adapter)
        assert contract["methods"] == ["send", "window_open"]
        assert contract["limits"]
    for adapter in adapters[3:]:
        assert adapter.api_status == "API_CONTRACT_UNVERIFIED"
        result = adapter.send("x", "oi", idempotency_key="k")
        assert result == {"status": "BLOCKED", "reasons": ["API_CONTRACT_UNVERIFIED"]}


def test_manual_channels_queue_the_draft_for_the_founder_to_paste(tmp_path):
    engine, _, _ = make_engine(
        tmp_path, mode="AUTO", channel="shopee", adapter=ManualPasteAdapter("shopee")
    )
    result = say(engine, "qual o prazo?", channel="shopee")

    assert result["action"] == "QUEUE"
    assert result["reason"] == "API_CONTRACT_UNVERIFIED"


def test_marketplace_gate_forbids_links_handles_and_contacts():
    for text in (
        "Chama no WhatsApp 11 99999-0000",
        "Veja em www.veratus.com.br",
        "Segue a gente no @veratus",
        "Manda e-mail para contato@veratus.com",
    ):
        assert "contato externo em marketplace" in hard_review_customer_reply(
            text, channel="mercado_livre"
        )
    assert (
        hard_review_customer_reply("Frete grátis para você.", channel="mercado_livre")
        == []
    )
    # Prazo, preço do site e pagamento são da venda direta, não do anúncio.
    for text in (
        "A entrega é em até 7 dias.",
        "Sai por R$ 289,90.",
        "Pode pagar no Pix.",
        "Mando o link de pagamento do Mercado Pago.",
    ):
        assert "condição da venda direta em marketplace" in (
            hard_review_customer_reply(text, channel="mercado_livre")
        )
        assert "condição da venda direta em marketplace" not in (
            hard_review_customer_reply(text, channel="whatsapp")
        )


def test_mercado_livre_answers_wait_for_the_flag():
    session = FakeSession()
    adapter = MercadoLivreConversationAdapter(
        token_provider=lambda: "t", seller_id="1", session=session, enabled=False
    )
    result = adapter.send("q-123", "Frete grátis.", idempotency_key="k")

    assert result["status"] == "BLOCKED"
    assert session.calls == []
    enabled = MercadoLivreConversationAdapter(
        token_provider=lambda: "t", seller_id="1", session=session, enabled=True
    )
    assert enabled.send("q-123", "Frete grátis.", idempotency_key="k")["status"] == (
        "SENT"
    )
    assert session.calls[-1][2]["json"] == {"question_id": 123, "text": "Frete grátis."}


def instagram_payload(mid, text, sender="IGSID-1", at=None):
    at = at or datetime.now(timezone.utc)
    return {
        "object": "instagram",
        "entry": [
            {
                "id": "IGUSER",
                "messaging": [
                    {
                        "sender": {"id": sender},
                        "timestamp": int(at.timestamp() * 1000),
                        "message": {"mid": mid, "text": text},
                    }
                ],
            }
        ],
    }


def test_instagram_direct_window_and_human_agent_tag(tmp_path):
    inbox = DirectInbox(tmp_path / "dm.sqlite3", salt="s", encryption_key=KEY)
    session = FakeSession({"/messages": {"message_id": "ig-out-1"}})
    adapter = InstagramDirectAdapter(
        inbox, access_token="t", ig_user_id="IGUSER", session=session, enabled=True
    )
    old = datetime.now(timezone.utc) - timedelta(days=2)
    assert adapter.receive(instagram_payload("mid-1", "oi", at=old)) == 1
    assert adapter.receive(instagram_payload("mid-1", "oi", at=old)) == 0
    conversation_id = adapter.pending()[0].conversation_id

    bot = adapter.send(conversation_id, "Olá!", idempotency_key="a")
    human = adapter.send(
        conversation_id,
        "Oi, aqui é da equipe.",
        idempotency_key="b",
        human_written=True,
    )

    assert bot["status"] == "BLOCKED"
    assert "CUSTOMER_SERVICE_WINDOW_CLOSED" in bot["reasons"]
    assert human["status"] == "SENT"
    assert session.calls[-1][2]["json"]["tag"] == "HUMAN_AGENT"
    assert (
        parse_instagram_messages(
            {
                "object": "instagram",
                "entry": [{"messaging": [{"message": {"is_echo": True}}]}],
            }
        )
        == []
    )


def test_instagram_direct_waits_for_its_flag(tmp_path):
    inbox = DirectInbox(tmp_path / "dm.sqlite3", salt="s", encryption_key=KEY)
    adapter = InstagramDirectAdapter(inbox, access_token="t", ig_user_id="U")
    adapter.receive(instagram_payload("mid-2", "oi"))
    conversation_id = adapter.pending()[0].conversation_id

    with patch.dict("os.environ", {"INSTAGRAM_MESSAGING_ENABLED": "false"}):
        result = adapter.send(conversation_id, "Olá!", idempotency_key="a")

    assert result["status"] == "BLOCKED"
    assert "INSTAGRAM_MESSAGING_DISABLED" in result["reasons"]


def test_founder_alert_uses_the_utility_template_then_email():
    session = FakeSession()
    client = WhatsAppCloudClient("t", "PNID", session=session)
    emails = []
    alert = {
        "channel": "whatsapp",
        "reason": "RECLAMACAO",
        "summary": "linha",
        "suggestion": "ok",
    }
    sender = FounderAlertSender(
        whatsapp_client=client,
        founder_number="5511900000000",
        whatsapp_enabled=True,
        email_sender=lambda subject, body: emails.append(subject),
    )
    assert sender(alert) == "WHATSAPP_TEMPLATE"
    template = session.calls[-1][2]["json"]["template"]
    assert template["name"] == "alerta_fundador"

    off = FounderAlertSender(
        whatsapp_client=client,
        founder_number="5511900000000",
        whatsapp_enabled=False,
        email_sender=lambda subject, body: emails.append(subject),
    )
    assert off(alert) == "EMAIL" and emails
    assert FounderAlertSender(whatsapp_enabled=False)(alert) is None


def test_marketing_needs_a_recorded_opt_in():
    assert not marketing_allowed(None)
    assert not marketing_allowed({"granted_at": "2026-10-13"})
    assert marketing_allowed({"granted_at": "2026-10-13", "source": "site"})


# -- com tudo desligado, nada sai ---------------------------------------------------


def _boom(*args, **kwargs):
    raise AssertionError("requisição externa de escrita com as chaves desligadas")


def test_flags_off_means_no_external_write(tmp_path):
    """Pipeline completo pelas rotas, com o cliente HTTP falhando em qualquer POST."""
    from integrations.webhook import app, rate_store

    rate_store.clear()
    env = {
        "VERATUS_ADMIN_TOKEN": "conv-admin",
        "VERATUS_AGENT_RUNTIME_DIR": str(tmp_path),
        "DATABASE_URL": "",
        "VERATUS_SESSION_SALT": "sal",
        "VERATUS_TOKEN_ENCRYPTION_KEY": KEY,
        "WHATSAPP_ACCESS_TOKEN": "token",
        "WHATSAPP_PHONE_NUMBER_ID": "PNID",
        "WHATSAPP_APP_SECRET": "",
        "WHATSAPP_SEND_ENABLED": "false",
        "PAYMENT_LINKS_ENABLED": "false",
        "MERCADO_PAGO_ACCESS_TOKEN": "mp",
        "CONVERSATION_MODE_WHATSAPP": "AUTO",
        "CONVERSATION_MODEL": "template",
        "OPENAI_API_KEY": "",
    }
    headers = {"X-Veratus-Admin-Token": "conv-admin"}
    with (
        patch.dict("os.environ", env, clear=False),
        patch("requests.Session.post", _boom),
        patch("requests.post", _boom),
    ):
        from integrations import webhook

        wa = webhook._whatsapp_channel()
        client = app.test_client()
        for index, text in enumerate(
            ["oi", "quero comprar o Ocean Blue", "Ana, 01310-100, Rua A 10"]
        ):
            wa.receive(wa_payload(f"wamid.off.{index}", text))
            processed = client.post(
                "/os/conversations/process",
                headers=headers,
                json={"channel": "whatsapp"},
            )
            assert processed.status_code == 200, processed.json
        queue = client.get("/os/conversations/queue", headers=headers).json["drafts"]
        metrics = client.get("/os/conversations/metrics", headers=headers)
        readiness = client.get("/os/conversations/readiness", headers=headers)
        adapters = client.get("/os/conversations/adapters", headers=headers)
        conversation_id = queue[0]["conv_key"].split(":", 1)[1]
        approve = client.post(
            f"/os/conversations/queue/{queue[0]['draft_id']}/approve",
            headers=headers,
            json={"actor": "fundador"},
        )
        handoff = client.post(
            f"/os/conversations/whatsapp/{conversation_id}/state",
            headers=headers,
            json={"state": "HUMAN", "actor": "fundador"},
        )
        alerts = client.get("/os/conversations/alerts", headers=headers)

    assert len(queue) == 3
    assert all("WHATSAPP_SEND_DISABLED" in (item["reason"] or "") for item in queue[:2])
    assert queue[2]["risk"] == 9  # sem link criado, o fundador decide
    assert metrics.status_code == 200
    assert readiness.json["channels"]["whatsapp"]["mode"] == "AUTO"
    assert adapters.json["adapters"]["shopee"]["api_status"] == (
        "API_CONTRACT_UNVERIFIED"
    )
    assert approve.json["status"] == "APPROVED"
    assert approve.json["external_write"] is False
    assert handoff.json["conversation"]["state"] == "HUMAN"
    assert alerts.status_code == 200


def test_mercado_pago_webhook_rejects_unsigned_calls():
    from integrations.webhook import app

    with patch.dict("os.environ", {"MERCADO_PAGO_WEBHOOK_SECRET": "segredo"}):
        response = app.test_client().post(
            "/integrations/mercado-pago/webhook?type=payment&data.id=1",
            json={"type": "payment", "data": {"id": "1"}},
        )
    assert response.status_code == 401
