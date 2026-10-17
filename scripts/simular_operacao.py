"""Modelo virtual de um dia da Veratus: o código real, com o mundo de fora em dublês.

Roda o serviço inteiro (integrations/webhook.py) num diretório temporário:
webhooks assinados do WhatsApp e do Instagram, perguntas do Mercado Livre, pedido,
link e webhook do Mercado Pago, e o envio marcado no painel. Toda chamada HTTP
para fora cai num roteador falso que registra e responde: nada sai da máquina.

Duas rodadas com os mesmos clientes:
- alvo: canais em AUTO e chaves de envio ligadas só dentro deste processo;
- hoje: os padrões de produção (SHADOW, envios desligados).

Clientes, números e CEPs são fictícios. O modelo é o TemplateSalesModel (texto
fixo, sem chave de API); em produção as respostas abertas vêm do modelo de
linguagem, com o mesmo gate.

Uso: python scripts/simular_operacao.py [--saida runtime/simulacao/operacao.json]
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import requests
from cryptography.fernet import Fernet

DEFAULT_OUTPUT = ROOT / "runtime" / "simulacao" / "operacao.json"
DEFAULT_DATE = "2026-10-20"
ADMIN = "simulacao-admin"
WA_SECRET = "simulacao-whatsapp"
IG_SECRET = "simulacao-instagram"
MP_SECRET = "simulacao-mercado-pago"
# Fixo para os ids de conversa saírem iguais entre execuções; não é segredo real.
SALT = "simulacao-veratus"
IG_OWN = "17800000000000000"
FOUNDER = "5500000000000"
PNID = "SIMPNID"
PAY_BASE = "https://pagamento.simulado.invalid/checkout"

SIMULATION_ENV = {
    "VERATUS_ADMIN_TOKEN": ADMIN,
    "DATABASE_URL": "",
    "VERATUS_SESSION_SALT": SALT,
    "WHATSAPP_ACCESS_TOKEN": "simulacao",
    "WHATSAPP_PHONE_NUMBER_ID": PNID,
    "WHATSAPP_APP_SECRET": WA_SECRET,
    "INSTAGRAM_APP_SECRET": IG_SECRET,
    "INSTAGRAM_USER_ID": IG_OWN,
    "INSTAGRAM_ACCESS_TOKEN": "simulacao",
    "MERCADO_PAGO_ACCESS_TOKEN": "simulacao",
    "MERCADO_PAGO_WEBHOOK_SECRET": MP_SECRET,
    "PUBLIC_BASE_URL": "https://veratus.simulado.invalid",
    "MERCADO_LIVRE_CLIENT_ID": "123456",
    "MERCADO_LIVRE_CLIENT_SECRET": "simulacao",
    "MERCADO_LIVRE_REDIRECT_URI": "https://veratus.simulado.invalid/callback",
    "MERCADO_LIVRE_SELLER_ID": "999",
    "MERCADO_LIVRE_NOTIFICATION_IP_ALLOWLIST": "",
    "FOUNDER_ALERT_WHATSAPP": FOUNDER,
    "FOUNDER_ALERT_EMAIL": "",
    "SMTP_HOST": "",
    "CONVERSATION_MODEL": "template",
    "OPENAI_API_KEY": "",
    "CONVERSATION_AUTOPROCESS_ENABLED": "false",
}
CHANNELS = ("whatsapp", "instagram", "mercado_livre", "shopee", "tiktok_shop", "amazon")
SEND_FLAGS = (
    "WHATSAPP_SEND_ENABLED",
    "INSTAGRAM_MESSAGING_ENABLED",
    "INSTAGRAM_DM_ENABLED",
    "MERCADO_LIVRE_ANSWERS_ENABLED",
    "PAYMENT_LINKS_ENABLED",
)
ROUNDS = {
    # Modo alvo: o que o fundador liga quando aprovar; aqui só dentro do processo.
    "alvo": {
        **{f"CONVERSATION_MODE_{c.upper()}": "AUTO" for c in CHANNELS},
        **{flag: "true" for flag in SEND_FLAGS},
    },
    # Hoje: os padrões de render.yaml.
    "hoje": {
        **{f"CONVERSATION_MODE_{c.upper()}": "SHADOW" for c in CHANNELS},
        **{flag: "false" for flag in SEND_FLAGS},
    },
}
AFTERSALES_INTENTS = frozenset({"status_pedido", "rastreio"})


@dataclass
class Cliente:
    key: str
    rotulo: str
    canal: str
    origem: str
    produto: str | None = None
    numero: str | None = None
    ig_id: str | None = None
    perguntas: list[int] = field(default_factory=list)
    conversas: set[str] = field(default_factory=set)
    pedido: str | None = None
    desfecho: str = "em conversa"


def clientes() -> dict[str, Cliente]:
    lista = [
        Cliente(
            "c1",
            "Cliente 1 · Site",
            "whatsapp",
            "site com referência VT",
            "royal-blue",
            numero="5511900000101",
        ),
        Cliente(
            "c2",
            "Cliente 2 · Anúncio",
            "whatsapp",
            "anúncio click-to-WhatsApp",
            "ocean-blue",
            numero="5511900000102",
        ),
        Cliente(
            "c3",
            "Cliente 3 · Instagram",
            "instagram",
            "comentário num post",
            ig_id="ig-cliente-3",
        ),
        Cliente(
            "c4",
            "Cliente 4 · Mercado Livre",
            "mercado_livre",
            "pergunta no anúncio",
            "lumiere-bracelet",
        ),
        Cliente(
            "c5",
            "Cliente 5 · Site",
            "whatsapp",
            "site com referência VT",
            "silver-prestige",
            numero="5511900000105",
        ),
        Cliente(
            "c6",
            "Cliente 6 · Site",
            "whatsapp",
            "site com referência VT",
            "arctic-white",
            numero="5511900000106",
        ),
        Cliente(
            "c7",
            "Cliente 7 · Anúncio",
            "whatsapp",
            "anúncio click-to-WhatsApp",
            "black-gmt",
            numero="5511900000107",
        ),
        Cliente(
            "c8",
            "Cliente 8 · Shopee",
            "shopee",
            "pergunta no anúncio (colada no painel)",
            "halo-verde-necklace",
        ),
        Cliente(
            "c9",
            "Cliente 9 · Site",
            "whatsapp",
            "site com referência VT",
            "polar-blue",
            numero="5511900000109",
        ),
    ]
    return {item.key: item for item in lista}


def _site_message(name: str, product_id: str, order: bool = False) -> str:
    """O texto que o botão do site preenche (landing/site.js)."""
    second = (
        "Quero fazer o pedido e confirmar a disponibilidade."
        if order
        else "Gostaria de confirmar disponibilidade e valor."
    )
    return (
        f"Olá! Vim pelo site da Veratus e tenho interesse em {name}.\n{second}\n"
        f"Referência da visita: VT-RJ5MO2 | produto={product_id}"
    )


AD = {"ctwa_clid": "simulado-anuncio-1", "source_type": "ad", "headline": "Veratus"}
# (hora, cliente, tipo, dados). Executado em ordem de hora: o dia intercalado.
ROTEIRO: list[tuple[str, str, str, dict[str, Any]]] = [
    ("09:04", "c1", "wa", {"texto": _site_message("Royal Blue", "royal-blue")}),
    ("09:06", "c1", "wa", {"texto": "Tem desconto no Pix?"}),
    ("09:08", "c1", "wa", {"texto": "Fechado, quero comprar"}),
    (
        "09:11",
        "c1",
        "wa",
        {"texto": "Cliente Um, CEP 01001-000, Praça da Sé, 100", "dados": True},
    ),
    ("09:24", "c1", "pago", {}),
    (
        "09:31",
        "c2",
        "wa",
        {"texto": "Oi, vi o anúncio do Ocean Blue. A caixa é de aço?", "referral": AD},
    ),
    ("09:33", "c2", "wa", {"texto": "E quanto custa?"}),
    ("09:36", "c2", "wa", {"texto": "Quero esse"}),
    (
        "09:39",
        "c2",
        "wa",
        {"texto": "Cliente Dois, 20040-020, Av. Rio Branco, 1", "dados": True},
    ),
    (
        "10:12",
        "c3",
        "comentario",
        {"texto": "quero ver a coleção", "media": "post-simulado-1"},
    ),
    ("10:20", "c3", "direct", {"texto": "Oi! Vi o post. Quais modelos vocês têm?"}),
    ("10:23", "c3", "direct", {"texto": "Quero falar com o dono antes de comprar"}),
    ("10:40", "c1", "envio", {}),
    (
        "11:05",
        "c4",
        "pergunta",
        {"id": 9001, "texto": "Essa pulseira serve em pulso fino? Qual o tamanho?"},
    ),
    (
        "11:07",
        "c4",
        "pergunta",
        {"id": 9002, "texto": "Me passa seu WhatsApp que eu compro direto"},
    ),
    (
        "11:09",
        "c4",
        "pergunta",
        {"id": 9003, "texto": "Qual o prazo de entrega para Curitiba?"},
    ),
    (
        "12:10",
        "c5",
        "wa",
        {"texto": _site_message("Silver Prestige", "silver-prestige", order=True)},
    ),
    (
        "12:13",
        "c5",
        "wa",
        {"texto": "Cliente Cinco, 30130-010, Rua da Bahia, 50", "dados": True},
    ),
    ("12:20", "c5", "wa", {"texto": "Já paguei, segue o comprovante"}),
    ("12:21", "c5", "pago", {}),
    (
        "13:30",
        "c6",
        "wa",
        {"texto": "Oi, quero o Arctic White. Manda primeiro que eu pago na entrega"},
    ),
    ("13:33", "c6", "wa", {"texto": "Entendi. Então pode fechar"}),
    ("14:05", "c7", "wa", {"texto": "Oi, quanto está o Black GMT?", "referral": AD}),
    ("14:07", "c7", "wa", {"texto": "Tem desconto?"}),
    ("14:09", "c7", "wa", {"texto": "Faz por 250?"}),
    ("14:11", "c7", "wa", {"texto": "Abaixa um pouco, vai"}),
    ("14:14", "c7", "wa", {"texto": "Não precisa chamar ninguém. Quero comprar"}),
    (
        "14:16",
        "c7",
        "wa",
        {"texto": "Cliente Sete, 40010-000, Av. Sete de Setembro, 7", "dados": True},
    ),
    ("14:30", "c7", "pago", {}),
    ("15:02", "c1", "wa", {"texto": "Oi! Qual o código de rastreio?"}),
    ("16:10", "c8", "colado", {"texto": "Qual o prazo de entrega? Tem frete grátis?"}),
    ("17:15", "c5", "envio", {}),
    ("17:16", "c7", "envio", {}),
    ("20:30", "c9", "wa", {"texto": _site_message("Polar Blue", "polar-blue")}),
    (
        "20:34",
        "c9",
        "wa",
        {"texto": "Meu primo comprou e não chegou. Se não resolver, vou no Procon"},
    ),
]

_REASONS = {
    "PEDIDO_DE_ATENDENTE": "o cliente pediu uma pessoa",
    "RECLAMACAO": "ameaça jurídica ou pública (Procon)",
    "PROBLEMA_PAGAMENTO": "conferir pagamento",
    "SO_PESSOA": "pedido especial",
    "FORA_DE_VENDAS": "assunto fora de vendas",
    "SINAL_DE_FRAUDE": "troca de endereço depois do pagamento",
    "FATO_NAO_CONFIRMADO": "informação ainda não confirmada",
    "ERRO_INTERNO": "erro interno",
    "ENVIAR_RASTREIO": "enviar o código de rastreio",
}


class FakeRouter:
    """Dublê da internet: Meta, Mercado Pago e Mercado Livre respondem daqui."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.outbox: list[dict[str, Any]] = []
        self.preferences: dict[str, dict[str, Any]] = {}
        self.questions: dict[int, dict[str, Any]] = {}
        self._counter = 0

    def _next(self) -> int:
        self._counter += 1
        return self._counter

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        parts = urlsplit(url)
        host, path = parts.hostname or "", parts.path
        body = kwargs.get("json") or {}
        method = method.upper()
        self.calls.append({"metodo": method, "host": host, "caminho": _path(path)})
        status, data = 404, {"error": "nao_simulado"}
        if host == "graph.facebook.com" and path.endswith("/messages"):
            if body.get("type") == "template":
                self.outbox.append(
                    {
                        "canal": "whatsapp",
                        "para": body.get("to"),
                        "template": body["template"]["name"],
                    }
                )
            else:
                self.outbox.append(
                    {
                        "canal": "whatsapp",
                        "para": body.get("to"),
                        "texto": body["text"]["body"],
                    }
                )
            status, data = 200, {"messages": [{"id": f"wamid.simulado.{self._next()}"}]}
        elif host == "graph.instagram.com" and path.endswith("/messages"):
            recipient = body.get("recipient") or {}
            self.outbox.append(
                {
                    "canal": "instagram",
                    "para": recipient.get("id"),
                    "comentario": recipient.get("comment_id"),
                    "texto": (body.get("message") or {}).get("text"),
                }
            )
            status, data = (
                200,
                {"message_id": f"ig.simulado.{self._next()}", "recipient_id": "x"},
            )
        elif host == "api.mercadopago.com" and path == "/checkout/preferences":
            pref = f"SIM-PREF-{len(self.preferences) + 1}"
            item = (body.get("items") or [{}])[0]
            self.preferences[pref] = {
                "order_id": body.get("external_reference"),
                "amount": item.get("unit_price"),
            }
            status, data = 200, {"id": pref, "init_point": f"{PAY_BASE}?pref={pref}"}
        elif host == "api.mercadopago.com" and path.startswith("/v1/payments/"):
            payment_id = path.rsplit("/", 1)[-1]
            pref = self.preferences.get(payment_id.replace("PAG-", "SIM-PREF-"))
            if pref:
                status, data = (
                    200,
                    {
                        "id": payment_id,
                        "status": "approved",
                        "external_reference": pref["order_id"],
                        "currency_id": "BRL",
                        "transaction_amount": pref["amount"],
                    },
                )
        elif host == "api.mercadolibre.com" and path == "/my/received_questions/search":
            status, data = (
                200,
                {
                    "questions": [
                        {
                            "id": qid,
                            "text": q["texto"],
                            "item_id": q["item"],
                            "status": "UNANSWERED",
                        }
                        for qid, q in sorted(self.questions.items())
                        if not q.get("respondida")
                    ]
                },
            )
        elif host == "api.mercadolibre.com" and path == "/answers":
            question = self.questions.get(int(body.get("question_id") or 0))
            if question is not None:
                question["respondida"] = True
            self.outbox.append(
                {
                    "canal": "mercado_livre",
                    "pergunta": body.get("question_id"),
                    "texto": body.get("text"),
                }
            )
            status, data = 200, {"id": body.get("question_id"), "status": "ANSWERED"}
        response = requests.Response()
        response.status_code = status
        response._content = json.dumps(data).encode("utf-8")
        response.headers["Content-Type"] = "application/json"
        response.url = url
        response.encoding = "utf-8"
        return response


def _path(path: str) -> str:
    """Caminho sem ids: o relatório mostra o tipo de chamada, não dados."""
    segments = []
    for segment in path.split("/"):
        if (
            segment.isdigit()
            or segment.startswith(("SIM-", "PAG-"))
            or segment in (IG_OWN, PNID)
        ):
            segments.append(":id")
        else:
            segments.append(segment)
    return "/".join(segments)


def _meta_headers(secret: str, body: bytes) -> dict[str, str]:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {
        "X-Hub-Signature-256": f"sha256={digest}",
        "Content-Type": "application/json",
    }


def _mp_headers(data_id: str) -> dict[str, str]:
    ts = str(int(time.time()))
    request_id = f"req-{data_id}"
    manifest = f"id:{data_id.lower()};request-id:{request_id};ts:{ts};"
    v1 = hmac.new(MP_SECRET.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return {"x-signature": f"ts={ts},v1={v1}", "x-request-id": request_id}


class Dia:
    """Uma rodada: o serviço inteiro num diretório temporário."""

    def __init__(self, nome: str, runtime: Path) -> None:
        from integrations import webhook

        self.nome = nome
        self.webhook = webhook
        self.client = webhook.app.test_client()
        self.router = FakeRouter()
        self.clientes = clientes()
        self.timeline: list[dict[str, Any]] = []
        self.runtime = runtime
        self.pedidos: dict[str, str] = {}
        self._outbox_seen = 0
        self._events_seen: set[str] = set()
        self._alerts_seen: set[str] = set()
        self._drafts_seen: set[str] = set()
        self._replies_seen: set[str] = set()
        self._wa_counter = 0
        self._conversa_cliente: dict[str, str] = {}

    # -- HTTP ---------------------------------------------------------------------

    def _post(self, path: str, **kwargs: Any) -> Any:
        self.webhook.rate_store.clear()
        return self.client.post(path, **kwargs)

    def _admin_get(self, path: str) -> Any:
        self.webhook.rate_store.clear()
        return self.client.get(path, headers={"X-Veratus-Admin-Token": ADMIN})

    def _admin_post(self, path: str, body: dict[str, Any]) -> Any:
        self.webhook.rate_store.clear()
        return self.client.post(
            path, headers={"X-Veratus-Admin-Token": ADMIN}, json=body
        )

    def _process(self, channel: str) -> list[dict[str, Any]]:
        response = self._admin_post("/os/conversations/process", {"channel": channel})
        if response.status_code != 200:
            raise RuntimeError(
                f"process {channel}: {response.status_code} {response.json}"
            )
        return response.json["results"]

    # -- entradas -------------------------------------------------------------------

    def wa(
        self, cliente: Cliente, texto: str, referral: dict[str, str] | None = None
    ) -> None:
        self._wa_counter += 1
        message = {
            "from": cliente.numero,
            "id": f"wamid.cliente.{self._wa_counter}",
            "timestamp": str(int(time.time())),
            "type": "text",
            "text": {"body": texto},
        }
        if referral:
            message["referral"] = referral
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {"changes": [{"field": "messages", "value": {"messages": [message]}}]}
            ],
        }
        body = json.dumps(payload).encode()
        response = self._post(
            "/integrations/whatsapp/webhook",
            data=body,
            headers=_meta_headers(WA_SECRET, body),
        )
        if response.status_code != 200:
            raise RuntimeError(f"webhook whatsapp: {response.status_code}")
        self._process("whatsapp")

    def comentario(self, cliente: Cliente, texto: str, media: str) -> None:
        payload = {
            "object": "instagram",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "comments",
                            "value": {
                                "id": f"comentario-{cliente.key}",
                                "text": texto,
                                "media": {"id": media},
                                "from": {
                                    "id": cliente.ig_id,
                                    "username": "cliente_ficticio",
                                },
                            },
                        }
                    ]
                }
            ],
        }
        body = json.dumps(payload).encode()
        self._post(
            "/integrations/instagram/webhook",
            data=body,
            headers=_meta_headers(IG_SECRET, body),
        )
        self._admin_post("/integrations/instagram/replies/process", {})

    def direct(self, cliente: Cliente, texto: str) -> None:
        self._wa_counter += 1
        # Evento de entrada: a conta da Veratus é quem recebe a mensagem do cliente.
        own_account = {"id": IG_OWN}
        payload = {
            "object": "instagram",
            "entry": [
                {
                    "messaging": [
                        {
                            "sender": {"id": cliente.ig_id},
                            "recipient": own_account,
                            "timestamp": int(time.time() * 1000),
                            "message": {
                                "mid": f"mid.cliente.{self._wa_counter}",
                                "text": texto,
                            },
                        }
                    ]
                }
            ],
        }
        body = json.dumps(payload).encode()
        self._post(
            "/integrations/instagram/webhook",
            data=body,
            headers=_meta_headers(IG_SECRET, body),
        )
        self._process("instagram")

    def pergunta(self, cliente: Cliente, question_id: int, texto: str) -> None:
        self.router.questions[question_id] = {"texto": texto, "item": "MLB-SIMULADO-1"}
        cliente.perguntas.append(question_id)
        self._post(
            "/integrations/mercado-livre/notifications",
            json={
                "_id": f"notificacao-{question_id}",
                "resource": f"/questions/{question_id}",
                "user_id": 999,
                "topic": "questions",
                "application_id": 123456,
            },
        )
        self._process("mercado_livre")

    def colado(self, cliente: Cliente, texto: str) -> None:
        # Shopee sem API de mensagens verificada: o operador cola a pergunta.
        from veratus_agents.conversations import InboundText

        engine = self.webhook._conversation_engine()
        engine.handle(
            InboundText(
                "shopee",
                f"shopee-{cliente.key}",
                f"shopee-{cliente.key}-1",
                texto,
                product_hint=cliente.produto,
            )
        )

    def pago(self, cliente: Cliente) -> bool:
        pref = next(
            (
                p
                for p, item in self.router.preferences.items()
                if self._pedido_cliente(item["order_id"]) == cliente.key
            ),
            None,
        )
        if pref is None:
            return False
        payment_id = pref.replace("SIM-PREF-", "PAG-")
        response = self._post(
            f"/integrations/mercado-pago/webhook?type=payment&data.id={payment_id}",
            json={"type": "payment", "data": {"id": payment_id}},
            headers=_mp_headers(payment_id),
        )
        return response.status_code == 200

    def envio(self, cliente: Cliente) -> bool:
        order_id = cliente.pedido
        if not order_id:
            return False
        order = self.webhook._order_ledger().get(order_id)
        if not order or order["status"] != "PAID":
            return False
        response = self._admin_post(
            f"/os/orders/{order_id}/status",
            {
                "event_id": f"envio-{order_id}",
                "status": "SHIPPED",
                "recorded_by": "operacao",
            },
        )
        return response.status_code == 200

    # -- leitura do que aconteceu --------------------------------------------------------

    def _pedido_cliente(self, order_id: str | None) -> str | None:
        for key, cliente in self.clientes.items():
            if cliente.pedido == order_id:
                return key
        return None

    def _label(self, order_id: str) -> str:
        if order_id not in self.pedidos:
            self.pedidos[order_id] = f"Pedido {len(self.pedidos) + 1}"
        return self.pedidos[order_id]

    def _add(
        self, t: str, cliente: Cliente, ator: str, tipo: str, texto: str, **extra: Any
    ) -> None:
        item = {
            "t": t,
            "canal": cliente.canal,
            "conversa": cliente.key,
            "cliente": cliente.rotulo,
            "ator": ator,
            "tipo": tipo,
            "texto": texto,
        }
        item.update({k: v for k, v in extra.items() if v is not None})
        self.timeline.append(item)

    def coletar(self, t: str, cliente: Cliente) -> None:
        """Tudo que o serviço fez nesta etapa, na ordem dos eventos."""
        engine = self.webhook._conversation_engine()
        store = engine.store
        outbox = self.router.outbox[self._outbox_seen :]
        self._outbox_seen = len(self.router.outbox)
        sent_texts = [o for o in outbox if o.get("texto") and not o.get("comentario")]
        drafts = {d["draft_id"]: d for d in store.queue()}
        alerts = [a for a in store.alerts() if a["alert_id"] not in self._alerts_seen]
        self._alerts_seen.update(a["alert_id"] for a in alerts)
        alerts.sort(key=lambda a: a["at"])

        # Comentário: a resposta privada sai pela fila de comentários, não pelo motor.
        for item in outbox:
            if item.get("comentario"):
                self._add(
                    t,
                    cliente,
                    "social-media-worker",
                    "resposta_enviada",
                    item["texto"],
                    via="resposta privada ao comentário",
                )
        for reply in self.webhook._instagram_queue().list_replies():
            if reply["comment_id"] in self._replies_seen:
                continue
            self._replies_seen.add(reply["comment_id"])
            if reply["status"] != "SENT":
                self._add(
                    t,
                    cliente,
                    "social-media-worker",
                    "rascunho",
                    "Resposta privada pronta, aguardando a chave de envio",
                    motivo=reply["status"],
                )

        events = [e for e in store.events() if e["event_id"] not in self._events_seen]
        self._events_seen.update(e["event_id"] for e in events)
        for event in events:
            if event["conv_key"]:
                self._conversa_cliente.setdefault(event["conv_key"], cliente.key)
                cliente.conversas.add(event["conv_key"])
            name = event["event"]
            detail = json.loads(event.get("detail_json") or "{}")
            ator = (
                "aftersales-agent"
                if event.get("intent") in AFTERSALES_INTENTS
                else "sales-agent"
            )
            if name == "CUSTOMER_DATA_RECEIVED":
                self._add(
                    t,
                    cliente,
                    "sales-agent",
                    "dados",
                    "Nome, CEP e endereço guardados cifrados; a mensagem sai da caixa do canal",
                )
            elif name == "ORDER_DRAFTED":
                cliente.pedido = detail.get("order_id")
                self._add(
                    t,
                    cliente,
                    "sales-agent",
                    "pedido",
                    f"{self._label(cliente.pedido)} registrado: {detail.get('sku')}",
                )
            elif name == "PAYMENT_LINK":
                if event["result"] == "CREATED":
                    self._add(
                        t,
                        cliente,
                        "sales-agent",
                        "link",
                        "Link do Mercado Pago criado (Pix ou cartão)",
                    )
                    cliente.desfecho = "aguardando pagamento"
                else:
                    cliente.desfecho = "pedido sem link (canal em SHADOW)"
                    self._add(
                        t,
                        cliente,
                        "sistema",
                        "bloqueio",
                        "Link de pagamento não criado",
                        motivo=event.get("reason"),
                    )
            elif name in ("REPLY_SENT",) or (
                name == "ORDER_PAID_NOTICE" and event["result"] == "SENT"
            ):
                texto = sent_texts.pop(0)["texto"] if sent_texts else ""
                self._add(
                    t,
                    cliente,
                    ator,
                    "resposta_enviada",
                    texto,
                    intencao=event.get("intent"),
                    risco=event.get("risk"),
                )
            elif name in ("DRAFT_QUEUED", "ORDER_PAID_NOTICE") or name in (
                "SEND_BLOCKED",
                "SEND_FAILED",
            ):
                draft_id = detail.get("draft_id")
                draft = drafts.get(draft_id) if draft_id else None
                if draft is None:
                    pending = [
                        d
                        for d in drafts.values()
                        if d["conv_key"] == event["conv_key"]
                        and d["draft_id"] not in self._drafts_seen
                    ]
                    draft = pending[-1] if pending else None
                if draft is not None:
                    self._drafts_seen.add(draft["draft_id"])
                motivo = (
                    event.get("reason")
                    or (draft or {}).get("reason")
                    or event.get("result")
                )
                self._add(
                    t,
                    cliente,
                    ator,
                    "rascunho",
                    (draft or {}).get("reply", ""),
                    intencao=event.get("intent"),
                    motivo=motivo,
                    risco=event.get("risk"),
                )
            elif name == "HANDOFF":
                alert = next(
                    (
                        a
                        for a in alerts
                        if a["conv_key"] == event["conv_key"]
                        and a["delivery"] != "PAINEL"
                    ),
                    None,
                )
                entrega = (alert or {}).get("delivery")
                self._add(
                    t,
                    cliente,
                    "customer-supervisor",
                    "chamada",
                    f"Fundador chamado: {_REASONS.get(event['reason'], event['reason'])}",
                    entrega=entrega,
                )
                cliente.desfecho = "com o fundador"
            elif name == "TASK":
                alert = next(
                    (
                        a
                        for a in alerts
                        if a["conv_key"] == event["conv_key"]
                        and a["delivery"] == "PAINEL"
                    ),
                    None,
                )
                self._add(
                    t,
                    cliente,
                    "customer-supervisor",
                    "tarefa",
                    (alert or {}).get("suggestion", _REASONS.get(event["reason"], "")),
                    motivo=_REASONS.get(event["reason"], event["reason"]),
                )
            elif name == "HANDOFF_OFFERED":
                self._add(
                    t,
                    cliente,
                    "sales-agent",
                    "oferta",
                    "A IA ofereceu chamar o responsável",
                )
            elif name == "ORDER_PAID":
                order_id = detail.get("order_id") or cliente.pedido
                self._add(
                    t,
                    cliente,
                    "mercado-pago",
                    "pagamento",
                    f"{self._label(order_id)} pago: webhook assinado e leitura de volta no Mercado Pago",
                )
                cliente.desfecho = "pago"
            elif name == "GATE_FAILED":
                self._add(
                    t,
                    cliente,
                    "sistema",
                    "gate",
                    "O filtro barrou um rascunho antes do envio",
                    motivo=event.get("reason"),
                )
            elif name == "SKIPPED_NOT_BOT":
                self._add(
                    t,
                    cliente,
                    "sistema",
                    "sistema",
                    "Conversa com o fundador: a IA não responde",
                )
            elif name == "CIRCUIT_BREAKER":
                self._add(
                    t,
                    cliente,
                    "customer-supervisor",
                    "disjuntor",
                    "Canal rebaixado um nível por segurança",
                    motivo=event.get("reason"),
                )

    # -- dia ------------------------------------------------------------------------

    def rodar(self) -> None:
        for t, key, tipo, dados in sorted(ROTEIRO, key=lambda step: step[0]):
            cliente = self.clientes[key]
            if tipo == "wa":
                texto = (
                    "Envia nome, CEP e endereço"
                    if dados.get("dados")
                    else dados["texto"]
                )
                self._add(
                    t,
                    cliente,
                    "cliente",
                    "mensagem_recebida",
                    texto,
                    origem=cliente.origem if len(cliente.conversas) == 0 else None,
                )
                self.wa(cliente, dados["texto"], dados.get("referral"))
            elif tipo == "comentario":
                self._add(
                    t,
                    cliente,
                    "cliente",
                    "comentario",
                    dados["texto"],
                    origem=cliente.origem,
                )
                self.comentario(cliente, dados["texto"], dados["media"])
            elif tipo == "direct":
                self._add(t, cliente, "cliente", "mensagem_recebida", dados["texto"])
                self.direct(cliente, dados["texto"])
            elif tipo == "pergunta":
                self._add(
                    t,
                    cliente,
                    "cliente",
                    "mensagem_recebida",
                    dados["texto"],
                    origem=cliente.origem if not cliente.perguntas else None,
                )
                self.pergunta(cliente, dados["id"], dados["texto"])
            elif tipo == "colado":
                self._add(
                    t,
                    cliente,
                    "cliente",
                    "mensagem_recebida",
                    dados["texto"],
                    origem=cliente.origem,
                )
                self.colado(cliente, dados["texto"])
            elif tipo == "pago":
                if not self.pago(cliente):
                    continue
            elif tipo == "envio":
                if self.envio(cliente):
                    self._add(
                        t,
                        cliente,
                        "operacao",
                        "envio",
                        f"{self._label(cliente.pedido)} despachado (marcado no painel)",
                    )
                    cliente.desfecho = "enviado"
                continue
            self.coletar(t, cliente)

    def resumo(self) -> dict[str, Any]:
        engine = self.webhook._conversation_engine()
        metrics = engine.metrics()
        orders = self.webhook._order_ledger().summary()
        by_status = orders.get("by_status", {})
        timeline = self.timeline
        conversas = [
            {
                "conversa": c.key,
                "cliente": c.rotulo,
                "canal": c.canal,
                "origem": c.origem,
                "produto": c.produto,
                "pedido": self.pedidos.get(c.pedido) if c.pedido else None,
                "desfecho": c.desfecho,
                "mensagens": sum(1 for item in timeline if item["conversa"] == c.key),
            }
            for c in self.clientes.values()
        ]
        contagem = {
            tipo: sum(1 for item in timeline if item["tipo"] == tipo)
            for tipo in (
                "resposta_enviada",
                "rascunho",
                "chamada",
                "tarefa",
                "bloqueio",
                "oferta",
            )
        }
        chamadas_http: dict[str, int] = {}
        for call in self.router.calls:
            label = f"{call['metodo']} {call['host']}{call['caminho']}"
            chamadas_http[label] = chamadas_http.get(label, 0) + 1
        return {
            "modo": "AUTO" if self.nome == "alvo" else "SHADOW",
            "linha_do_tempo": timeline,
            "conversas": conversas,
            "funil": {
                "leads": len(self.clientes),
                "conversas": sum(m["conversations"] for m in metrics.values()),
                "pedidos": orders["orders"],
                "pagos": sum(
                    by_status.get(s, 0) for s in ("PAID", "SHIPPED", "DELIVERED")
                ),
                "enviados": sum(by_status.get(s, 0) for s in ("SHIPPED", "DELIVERED")),
                "receita_confirmada_brl": orders["confirmed_net_merchandise_brl"],
                "chamadas_ao_fundador": contagem["chamada"],
                "tarefas_no_painel": contagem["tarefa"],
                "respostas_enviadas": contagem["resposta_enviada"],
                "rascunhos": contagem["rascunho"],
            },
            "metricas_por_canal": {
                channel: {
                    key: value
                    for key, value in data.items()
                    if key
                    in (
                        "conversations",
                        "handoffs_by_reason",
                        "tasks_by_reason",
                        "conversation_to_order",
                        "orders_paid",
                        "gate_failures",
                    )
                }
                for channel, data in metrics.items()
                if data["conversations"]
            },
            "chamadas_http": [
                {"chamada": label, "vezes": count}
                for label, count in sorted(chamadas_http.items())
            ],
        }


def _rodada(nome: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
        env = {
            **SIMULATION_ENV,
            **ROUNDS[nome],
            "VERATUS_AGENT_RUNTIME_DIR": tmp,
            "VERATUS_TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii"),
        }
        stack.enter_context(patch.dict(os.environ, env))
        dia = Dia(nome, Path(tmp))
        stack.enter_context(
            patch.object(requests.Session, "request", dia.router.request)
        )
        # Conta do Mercado Livre conectada só nesta caixa de areia.
        dia.webhook._mercado_oauth_store().save_tokens(
            {
                "access_token": "simulacao",
                "refresh_token": "simulacao",
                "expires_in": 21600,
                "user_id": 999,
            }
        )
        dia.rodar()
        resumo = dia.resumo()
        if nome == "alvo":
            from veratus_agents.sales_flow import flow_report

            resumo["fluxo"] = flow_report(
                dia.webhook._conversation_engine(), dia.webhook._order_ledger()
            )
        return resumo


def simular(data: str = DEFAULT_DATE) -> dict[str, Any]:
    alvo = _rodada("alvo")
    hoje = _rodada("hoje")
    fluxo = alvo.pop("fluxo")
    return {
        "schema": "veratus.simulacao/v1",
        "meta": {
            "data_simulada": data,
            "modelo": "template-v1 (texto fixo; em produção as respostas abertas vêm do modelo de linguagem com o mesmo gate)",
            "aviso": (
                "Simulação com clientes fictícios. O código é o do serviço; as chaves de "
                "envio e o modo AUTO foram ligados só dentro deste processo, e toda "
                "chamada para Meta, Mercado Pago e Mercado Livre foi para um dublê local."
            ),
            "rodadas": {
                "alvo": "canais em AUTO e envios ligados (o que acontece depois da aprovação do fundador)",
                "hoje": "padrões de produção: SHADOW e envios desligados",
            },
        },
        "fluxo": {
            "stages": fluxo["stages"],
            "supervisor": fluxo["supervisor"],
            "roles": fluxo["roles"],
            "channels": [
                {
                    k: v
                    for k, v in channel.items()
                    if k not in ("metrics", "send_enabled", "mode")
                }
                for channel in fluxo["channels"]
            ],
        },
        "rodadas": {"alvo": alvo, "hoje": hoje},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--saida", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--data", default=DEFAULT_DATE)
    args = parser.parse_args(argv)
    result = simular(args.data)
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.saida.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(args.saida)
    for nome, rodada in result["rodadas"].items():
        funil = rodada["funil"]
        print(
            f"{nome}: {funil['leads']} leads, {funil['conversas']} conversas, "
            f"{funil['pedidos']} pedidos, {funil['pagos']} pagos, {funil['enviados']} enviados, "
            f"{funil['chamadas_ao_fundador']} chamadas ao fundador, {funil['tarefas_no_painel']} tarefas"
        )
    print(f"gravado em {args.saida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
