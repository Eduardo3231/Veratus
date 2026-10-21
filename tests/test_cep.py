"""CEP no fechamento: confere antes do pedido, sem mandar nome nem endereço."""

from __future__ import annotations

import requests
from cryptography.fernet import Fernet

from veratus_agents import cep
from veratus_agents.conversations import (
    ConversationEngine,
    ConversationStore,
    InboundText,
    TemplateSalesModel,
)

KEY = Fernet.generate_key().decode("ascii")


class _Response:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return self._body


class _Session:
    def __init__(self, *responses):
        self.responses, self.urls = list(responses), []

    def get(self, url, timeout):
        self.urls.append(url)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_lookup_sends_only_the_cep_and_falls_back_to_viacep():
    assert cep.lookup("CEP 01310-100")["status"] == "DISABLED"
    found = _Session(_Response(200, {"city": "São Paulo", "state": "SP"}))
    text = "Maria, CEP 01310-100, Av. Paulista 1000"
    assert cep.lookup(text, session=found, enabled=True) == {
        "status": "FOUND",
        "city": "São Paulo",
        "state": "SP",
    }
    assert found.urls == ["https://brasilapi.com.br/api/cep/v2/01310100"]
    fallback = _Session(requests.ConnectionError(), _Response(200, {"erro": True}))
    assert cep.lookup(text, session=fallback, enabled=True)["status"] == "NOT_FOUND"
    assert "viacep.com.br/ws/01310100/json/" in fallback.urls[1]
    down = _Session(requests.Timeout(), requests.Timeout())
    assert cep.lookup(text, session=down, enabled=True)["status"] == "UNAVAILABLE"
    assert cep.lookup("sem cep", enabled=True)["status"] == "INVALID"


def test_engine_asks_again_for_a_cep_that_does_not_exist(tmp_path):
    sent, orders = [], []

    class _Adapter:
        api_status = "SIMULATED"

        def __init__(self):
            self.limits = {"max_text": 4096}

        def window_open(self, conversation_id, now):
            return True

        def send(self, conversation_id, text, *, idempotency_key, human_written=False):
            sent.append(text)
            return {"status": "SENT", "message_id": f"m{len(sent)}"}

    def order_factory(channel, conversation_id, product):
        orders.append(product["id"])
        return {"order_id": "ord-cep-1", "sku": product["id"]}

    answers = iter(
        [{"status": "NOT_FOUND"}, {"status": "FOUND", "city": "Recife", "state": "PE"}]
    )
    engine = ConversationEngine(
        ConversationStore(tmp_path / "cv.sqlite3", encryption_key=KEY),
        adapters={"whatsapp": _Adapter()},
        model=TemplateSalesModel(),
        alert_sender=lambda alert: "TEST",
        mode_resolver=lambda _channel: "AUTO",
        knowledge_context=dict,
        order_factory=order_factory,
        payment_linker=lambda order, product: {
            "status": "CREATED",
            "url": "https://mp.test/p/1",
        },
        cep_lookup=lambda text: next(answers),
    )
    for index, text in enumerate(
        [
            "quero comprar o Ocean Blue",
            "Ana Lima, CEP 50000-999, Rua A 10",
            "Ana Lima, CEP 50030-230, Rua A 10",
        ]
    ):
        result = engine.handle(InboundText("whatsapp", "wa_1", f"m{index}", text))
        assert result["intent"] in ("fechamento", "dados_pedido")
    assert "conferir os 8 números" in sent[1]
    assert len(orders) == 1  # só depois do CEP que existe
    assert "para entrega em Recife/PE" in sent[2] and "https://mp.test/p/1" in sent[2]
