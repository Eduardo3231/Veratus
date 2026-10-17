"""Fluxo contínuo: token do Mercado Livre, processamento por webhook e resiliência."""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import patch

from cryptography.fernet import Fernet

from integrations import webhook
from veratus_agents.conversations import (
    ConversationEngine,
    ConversationStore,
    InboundText,
    TemplateSalesModel,
    classify,
)

KEY = Fernet.generate_key().decode("ascii")


# -- Mercado Livre: o provedor de token recebe o store -------------------------------


def test_mercado_livre_token_provider_builds_the_manager_with_the_store():
    store = object()
    app_env = {
        "MERCADO_LIVRE_CLIENT_ID": "123",
        "MERCADO_LIVRE_CLIENT_SECRET": "segredo-de-teste",
        "MERCADO_LIVRE_REDIRECT_URI": "https://exemplo.invalid/callback",
    }
    with (
        patch.dict("os.environ", app_env),
        patch(
            "veratus_agents.mercado_livre_oauth.persisted_credentials_available",
            return_value=True,
        ),
        patch.object(webhook, "_mercado_oauth_store", return_value=store),
    ):
        provider = webhook._mercado_livre_token_provider()
    # Antes, from_env() sem store levantava TypeError e o canal ficava mudo.
    assert callable(provider)
    assert provider.__self__.store is store


def test_mercado_livre_token_provider_without_connection_is_none():
    with patch(
        "veratus_agents.mercado_livre_oauth.persisted_credentials_available",
        return_value=False,
    ):
        assert webhook._mercado_livre_token_provider() is None


# -- processamento contínuo ------------------------------------------------------------


def _reset_autoprocess():
    webhook._autoprocess_running.clear()
    webhook._autoprocess_again.clear()


def test_autoprocess_is_off_by_default():
    _reset_autoprocess()
    started = []
    with (
        patch.dict("os.environ", {"CONVERSATION_AUTOPROCESS_ENABLED": "false"}),
        patch.object(webhook, "_start_autoprocess_thread", started.append),
    ):
        assert webhook._autoprocess("whatsapp") is False
    assert started == []


def test_autoprocess_runs_one_worker_per_channel_and_picks_up_late_messages():
    _reset_autoprocess()
    started, processed = [], []
    with (
        patch.dict("os.environ", {"CONVERSATION_AUTOPROCESS_ENABLED": "true"}),
        patch.object(webhook, "_start_autoprocess_thread", started.append),
    ):
        assert webhook._autoprocess("whatsapp") is True
        # Mensagem nova com o canal ainda rodando: não abre outra thread.
        assert webhook._autoprocess("whatsapp") is True
        assert webhook._autoprocess("instagram") is True
    assert started == ["whatsapp", "instagram"]
    with (
        patch.object(webhook, "_conversation_engine", return_value="engine"),
        patch.object(
            webhook,
            "_process_channel",
            lambda engine, channel: processed.append(channel),
        ),
    ):
        webhook._autoprocess_worker("whatsapp")
    # Uma volta para o pedido original e outra para o que chegou no meio.
    assert processed == ["whatsapp", "whatsapp"]
    assert "whatsapp" not in webhook._autoprocess_running
    _reset_autoprocess()


def test_autoprocess_worker_survives_an_engine_error():
    _reset_autoprocess()
    webhook._autoprocess_running.add("whatsapp")

    def broken():
        raise ValueError("conversations_not_configured")

    with patch.object(webhook, "_conversation_engine", broken):
        webhook._autoprocess_worker("whatsapp")
    assert "whatsapp" not in webhook._autoprocess_running


def _signed(secret, body):
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {
        "X-Hub-Signature-256": f"sha256={digest}",
        "Content-Type": "application/json",
    }


def test_whatsapp_webhook_triggers_the_channel_when_a_message_arrives(tmp_path):
    _reset_autoprocess()
    env = {
        "VERATUS_AGENT_RUNTIME_DIR": str(tmp_path),
        "DATABASE_URL": "",
        "VERATUS_SESSION_SALT": "sal",
        "VERATUS_TOKEN_ENCRYPTION_KEY": KEY,
        "WHATSAPP_APP_SECRET": "segredo-wa",
        "CONVERSATION_AUTOPROCESS_ENABLED": "true",
    }
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messages": [
                                {
                                    "from": "5511999990000",
                                    "id": "wamid.auto.1",
                                    "timestamp": "1760000000",
                                    "type": "text",
                                    "text": {"body": "oi"},
                                }
                            ]
                        },
                    }
                ]
            }
        ],
    }
    body = json.dumps(payload).encode()
    triggered = []
    with (
        patch.dict("os.environ", env),
        patch.object(webhook, "_autoprocess", triggered.append),
    ):
        response = webhook.app.test_client().post(
            "/integrations/whatsapp/webhook",
            data=body,
            headers=_signed("segredo-wa", body),
        )
        repeat = webhook.app.test_client().post(
            "/integrations/whatsapp/webhook",
            data=body,
            headers=_signed("segredo-wa", body),
        )
    assert response.status_code == 200, response.json
    assert repeat.status_code == 200
    # O reenvio da Meta é duplicado: só a primeira entrega dispara o canal.
    assert triggered == ["whatsapp"]


def test_mercado_livre_question_notification_triggers_the_channel(tmp_path):
    env = {
        "VERATUS_AGENT_RUNTIME_DIR": str(tmp_path),
        "DATABASE_URL": "",
        "VERATUS_TOKEN_ENCRYPTION_KEY": KEY,
        "MERCADO_LIVRE_CLIENT_ID": "123",
        "MERCADO_LIVRE_NOTIFICATION_IP_ALLOWLIST": "",
    }
    triggered = []

    def notify(topic, event):
        return webhook.app.test_client().post(
            "/integrations/mercado-livre/notifications",
            json={
                "_id": event,
                "resource": f"/{topic}/1",
                "user_id": 9,
                "topic": topic,
                "application_id": 123,
            },
        )

    with (
        patch.dict("os.environ", env),
        patch.object(webhook, "_autoprocess", triggered.append),
    ):
        question = notify("questions", "evento-1")
        order = notify("orders_v2", "evento-2")
    assert question.status_code == 200, question.json
    assert order.status_code == 200, order.json
    assert triggered == ["mercado_livre"]


# -- resiliência -------------------------------------------------------------------------


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


class _BrokenModel:
    name = "sales-v2-live"

    def draft(self, request):
        raise TimeoutError("modelo fora do ar")

    def rewrite(self, request, draft, issues):
        raise TimeoutError("modelo fora do ar")


def _engine(tmp_path, adapter, model=None):
    store = ConversationStore(tmp_path / "cv.sqlite3", encryption_key=KEY)
    return ConversationEngine(
        store,
        adapters={"whatsapp": adapter},
        model=model or TemplateSalesModel(),
        alert_sender=lambda alert: "TEST",
        mode_resolver=lambda _channel: "AUTO",
        knowledge_context=dict,
    )


def test_model_failure_falls_back_to_the_fixed_reply(tmp_path):
    adapter = _Adapter()
    engine = _engine(tmp_path, adapter, model=_BrokenModel())
    result = engine.handle(
        InboundText("whatsapp", "c1", "m1", "vocês fazem entrega em Manaus de barco?")
    )
    assert result["action"] == "SEND"
    assert adapter.sent  # o cliente recebe a resposta fixa, que passa no gate
    failed = engine.store.events(event="MODEL_FAILED")
    assert failed and failed[0]["result"] == "TimeoutError"


def test_one_broken_message_does_not_stop_the_channel(tmp_path):
    inbound = [
        InboundText("whatsapp", "c1", "m1", "oi"),
        InboundText("whatsapp", "c2", "m2", "oi"),
    ]
    adapter = _Adapter(inbound)
    engine = _engine(tmp_path, adapter)
    original = engine.handle

    def handle(item):
        if item.message_id == "m1":
            raise RuntimeError("erro interno")
        return original(item)

    engine.handle = handle
    results = webhook._process_channel(engine, "whatsapp")
    assert [item["action"] for item in results] == ["ERROR", "SEND"]
    assert adapter.marks == {"m1": "V2_ERROR", "m2": "V2_SEND"}
    tasks = [alert for alert in engine.store.alerts() if alert["delivery"] == "PAINEL"]
    assert [task["reason"] for task in tasks] == ["ERRO_INTERNO"]
    assert tasks[0]["summary"] == ""  # sem texto do cliente na tarefa


def test_purchase_process_question_is_not_a_legal_threat():
    assert classify("qual o processo de compra?", {}).trigger is None
    assert classify("vou abrir um processo contra vocês", {}).trigger == "RECLAMACAO"
    assert classify("vou te colocar na justiça", {}).trigger == "RECLAMACAO"


def test_marketplace_contact_request_stays_in_the_listing(tmp_path):
    adapter = _Adapter()
    store = ConversationStore(tmp_path / "cv.sqlite3", encryption_key=KEY)
    engine = ConversationEngine(
        store,
        adapters={"mercado_livre": adapter},
        model=TemplateSalesModel(),
        alert_sender=lambda alert: "TEST",
        mode_resolver=lambda _channel: "AUTO",
        knowledge_context=dict,
    )
    result = engine.handle(
        InboundText(
            "mercado_livre", "q-1", "q-1", "me passa seu whatsapp que eu compro"
        )
    )
    assert result["action"] == "SEND"
    assert result["intent"] == "contato_marketplace"
    assert adapter.sent == [
        (
            "Por aqui mesmo eu te ajudo, e a compra é feita pelo próprio anúncio. "
            "O que você quer saber da peça?"
        )
    ]
    # Fora do marketplace o pedido de contato não muda nada.
    assert classify("me passa seu whatsapp", {"channel": "whatsapp"}).intent == "outro"


def test_marketplace_replies_never_promise_direct_sale_terms():
    import re

    from veratus_agents.catalog import find_product
    from veratus_agents.conversations import INTENT_RISK, DraftRequest
    from veratus_agents.policy import hard_review_customer_reply

    model = TemplateSalesModel()
    direct_terms = re.compile(
        r"7 dias|R\$|\bpix\b|mercado pago|link de pagamento", re.IGNORECASE
    )
    for channel in ("mercado_livre", "shopee", "tiktok_shop", "amazon"):
        for product_id in ("ocean-blue", "lumiere-bracelet", None):
            product = find_product(product_id) if product_id else None
            for intent in INTENT_RISK:
                for discount in (False, True):
                    draft = model.draft(
                        DraftRequest(
                            channel=channel,
                            conversation_id="q-1",
                            message="pergunta",
                            intent=intent,
                            topic="as medidas",
                            product=product,
                            context={},
                            order=None,
                            discount_request=discount,
                        )
                    )
                    reply = draft["customer_reply"]
                    assert not direct_terms.search(reply), (channel, intent, reply)
                    assert not hard_review_customer_reply(
                        reply,
                        draft.get("commercial_claims"),
                        product_collection=(product or {}).get("collection"),
                        channel=channel,
                    ), (channel, intent, reply)
