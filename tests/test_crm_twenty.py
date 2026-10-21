"""Twenty (CRM): conversa vira lead pseudônimo e o estágio só anda para a frente."""

from __future__ import annotations

import json
from types import SimpleNamespace

from cryptography.fernet import Fernet

from veratus_agents.conversations import ConversationStore
from veratus_agents.crm_twenty import (
    FUNNEL_STAGES,
    TwentyClient,
    TwentySync,
    funnel_step,
)

KEY = Fernet.generate_key().decode("ascii")
API = "https://crm.example.test"


class _Response:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class _Session:
    """Twenty de mentira: grava cada chamada e devolve ids."""

    def __init__(self, *, stage="NEW", fail_opportunity=0):
        self.calls, self.stage, self.fail_opportunity = [], stage, fail_opportunity

    def request(self, method, url, *, json=None, headers=None, **_):
        self.calls.append({"method": method, "url": url, "json": json, "h": headers})
        if method == "POST" and url.endswith("/people"):
            return _Response(201, {"data": {"createPerson": {"id": "p1"}}})
        if method == "POST":
            if self.fail_opportunity:
                self.fail_opportunity -= 1
                return _Response(500, {})
            return _Response(201, {"data": {"createOpportunity": {"id": "o1"}}})
        if method == "GET":
            return _Response(200, {"data": {"opportunity": {"stage": self.stage}}})
        return _Response(200, {"data": {"updateOpportunity": {"id": "o1"}}})


def _engine(tmp_path):
    store = ConversationStore(tmp_path / "ops.sqlite3", encryption_key=KEY)
    return SimpleNamespace(store=store)


def _sync(tmp_path, session, *, enabled=True, url=API):
    client = TwentyClient(url, "twenty-test-key", session=session)
    return TwentySync(tmp_path / "crm.sqlite3", client, enabled=enabled)


def test_flag_off_or_without_https_sends_nothing(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    engine.store.conversation("whatsapp", "c1")
    session = _Session()
    result = _sync(tmp_path, session, enabled=False).sync(engine)
    assert result["status"] == "DISABLED"
    assert result["preview"][0]["action"] == "CREATE"
    plain = _sync(tmp_path, session, url="http://crm.example.test")
    assert plain.sync(engine)["status"] == "NOT_CONFIGURED"
    monkeypatch.setenv("VERATUS_AGENT_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("TWENTY_SYNC_ENABLED", "1")  # só o literal "true" liga
    monkeypatch.setenv("TWENTY_API_URL", API)
    monkeypatch.setenv("TWENTY_API_KEY", "twenty-test-key")
    assert TwentySync.from_env().sync(engine)["status"] == "DISABLED"
    assert session.calls == []


def test_lead_is_pseudonymous_and_carries_only_the_public_price(tmp_path):
    engine, session = _engine(tmp_path), _Session()
    phone = "55" + "11" + "9" + "8765" + "4321"
    document = f"{123_456_789}{9:02d}"  # CPF montado em tempo de execução
    conversation_id = f"wa-{phone}"
    key = engine.store.conversation("whatsapp", conversation_id)["conv_key"]
    origin = {
        "utm": {"utm_source": "instagram", "utm_medium": f"ana@mail.test {document}"},
        "visit_ref": phone,
    }
    engine.store.update(key, product_id="black-gmt", origin_json=json.dumps(origin))
    assert _sync(tmp_path, session).sync(engine)["created"] == 1
    sent = json.dumps([call["json"] for call in session.calls])
    for private in (phone, "98765", document, "ana@", "mail.test", conversation_id):
        assert private not in sent
    person, opportunity = (call["json"] for call in session.calls)
    assert person["name"]["firstName"] == "Cliente"
    assert person["name"]["lastName"].startswith("WhatsApp ")
    assert opportunity["name"].startswith("Black GMT · WhatsApp ")
    assert opportunity["name"].endswith(" · via instagram")
    assert opportunity["stage"] == "SCREENING"
    assert opportunity["amount"] == {"amountMicros": 289_900_000, "currencyCode": "BRL"}
    assert opportunity["pointOfContactId"] == "p1"
    assert session.calls[0]["h"] == {"Authorization": "Bearer twenty-test-key"}


def test_funnel_maps_to_the_twenty_stages():
    def stage(row=None, *events):
        base = {"paid": 0, "order_id": None, "product_id": None}
        return FUNNEL_STAGES[funnel_step({**base, **(row or {})}, list(events))]

    assert stage() == "NEW"
    assert stage({}, {"event": "REPLY_SENT", "intent": "preco"}) == "SCREENING"
    assert stage({"product_id": "black-gmt"}) == "SCREENING"
    assert stage({}, {"event": "DRAFT_QUEUED", "intent": "fechamento"}) == "MEETING"
    assert stage({}, {"event": "PAYMENT_LINK", "result": "CREATED"}) == "PROPOSAL"
    assert stage({"order_id": "ord-1"}) == "PROPOSAL"
    assert stage({"order_id": "ord-1", "paid": 1}) == "CUSTOMER"
    assert stage({}, {"event": "ORDER_PAID"}) == "CUSTOMER"


def test_resync_never_duplicates_nor_moves_a_card_back(tmp_path):
    engine = _engine(tmp_path)
    store = engine.store
    key = store.conversation("facebook", "fb_c1")["conv_key"]
    store.event("facebook", key, "REPLY_SENT", intent="preco")
    session = _Session(fail_opportunity=1)
    sync = _sync(tmp_path, session)
    assert sync.sync(engine)["failed"] == 1  # Pessoa criada, Oportunidade falhou
    assert sync.sync(engine)["created"] == 1  # só a Oportunidade: sem Pessoa nova
    posts = [call["url"] for call in session.calls if call["method"] == "POST"]
    assert [url.rsplit("/", 1)[1] for url in posts] == [
        "people",
        "opportunities",
        "opportunities",
    ]
    seen = len(session.calls)
    assert sync.sync(engine)["unchanged"] == 1
    assert len(session.calls) == seen  # nada mudou, nada sai

    store.event("facebook", key, "REPLY_SENT", intent="fechamento")
    session.stage = "SCREENING"
    assert sync.sync(engine)["updated"] == 1
    patch = session.calls[-1]
    assert (patch["method"], patch["json"]) == ("PATCH", {"stage": "MEETING"})

    session.stage = "CUSTOMER"  # o fundador moveu o card à mão
    store.update(key, order_id="ord-1")
    seen = len(session.calls)
    assert sync.sync(engine)["unchanged"] == 1
    assert [call["method"] for call in session.calls[seen:]] == ["GET"]
    store.update(key, paid=1)
    seen = len(session.calls)
    sync.sync(engine)
    assert len(session.calls) == seen
