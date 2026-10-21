"""Twenty (CRM de vendas): cada conversa da Veratus vira um lead no funil.

O Twenty (twentyhq/twenty) guarda o funil: cada conversa, de qualquer canal, vira
uma Pessoa e uma Oportunidade cujo estágio segue o funil, para o fundador ver os
leads por estágio, canal de origem e valor.

Minimização (LGPD): nenhum contato do cliente sai daqui. A Pessoa é um pseudônimo
("Cliente" + canal + id curto, um HMAC com segredo que só existe no banco local)
e o valor é o preço público do produto, nunca custo ou margem. O estágio só anda
para a frente, nem por cima de quem moveu o card à mão no Twenty. Desligado por
padrão (``TWENTY_SYNC_ENABLED``): desligado, nada sai e ``sync`` devolve a prévia.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

from .catalog import load_catalog, public_price
from .conversations import summarize
from .meta_webhooks import env_flag, normalize_text
from .sqlstore import SqlStore

# Etapas do funil da Veratus, da primeira à última.
FUNNEL_ORDER = ("nova_conversa", "interesse", "fechamento", "proposta", "pago")
# Etapa -> estágio padrão da Oportunidade no Twenty (mude aqui se o pipeline mudar).
FUNNEL_STAGES = {
    "nova_conversa": "NEW",
    "interesse": "SCREENING",
    "fechamento": "MEETING",
    "proposta": "PROPOSAL",
    "pago": "CUSTOMER",
}
# Perguntou valor ou disponibilidade: interesse. Disse "sim": fechamento.
INTEREST_INTENTS = frozenset(
    {"preco", "disponibilidade", "produto", "como_pedir", "objecao_preco"}
)
CLOSING_INTENTS = frozenset({"fechamento", "dados_pedido", "link_pagamento"})
CHANNEL_LABELS = {
    "facebook": "Facebook",
    "whatsapp": "WhatsApp",
    "instagram": "Instagram",
    "mercado_livre": "Mercado Livre",
    "shopee": "Shopee",
    "tiktok_shop": "TikTok Shop",
    "amazon": "Amazon",
}
CURRENCY = "BRL"
MICROS = 1_000_000
TIMEOUT_SECONDS = 10
# Escritas por sync (até 2 chamadas cada); o resto fica para o próximo, abaixo do
# limite de taxa do Twenty.
MAX_WRITES = 25
# Reserva de uma conversa durante a escrita; vencida, outro sync pode tentar.
CLAIM_TTL = timedelta(minutes=10)
# Oportunidade apagada no Twenty pelo fundador: não volta.
REMOVED = "REMOVIDO"
# Origem (utm ou Direct) só como rótulo curto; texto livre do cliente não passa.
_LABEL = re.compile(r"[a-z][a-z0-9_.-]{0,29}")
_DIGITS = re.compile(r"\d{3}")
_LETTERS = str.maketrans("0123456789", "ghijklmnop")

SCHEMA = """
CREATE TABLE IF NOT EXISTS crm_twenty_links (
    conv_key TEXT PRIMARY KEY,
    person_id TEXT,
    opportunity_id TEXT,
    last_stage TEXT,
    last_rank INTEGER NOT NULL DEFAULT -1,
    amount_micros BIGINT,
    claimed_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS crm_twenty_secret (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""
_LINK_FIELDS = frozenset(
    {"person_id", "opportunity_id", "last_stage", "last_rank", "amount_micros"}
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TwentyError(Exception):
    """Falha com o Twenty; a mensagem nunca leva a chave nem dado de cliente."""

    def __init__(self, reason: str, status: int | None = None) -> None:
        super().__init__(reason)
        self.reason, self.status = reason, status


class TwentyClient:
    """REST do Twenty com a chave de API (Bearer); a sessão HTTP é injetável."""

    def __init__(
        self,
        api_url: str,
        api_key: str,
        *,
        session: Any = None,
        timeout: float = TIMEOUT_SECONDS,
    ) -> None:
        self.api_url = (api_url or "").strip().rstrip("/")
        self._api_key = (api_key or "").strip()
        self._session = session
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        """Só com HTTPS e chave: a chave não viaja em texto aberto."""
        return self.api_url.startswith("https://") and bool(self._api_key)

    def _call(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if not self.configured:
            raise TwentyError("NOT_CONFIGURED")
        if self._session is None:
            self._session = requests.Session()
        try:
            response = self._session.request(
                method,
                f"{self.api_url}/rest/{path}",
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self.timeout,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise TwentyError(type(exc).__name__) from None
        if response.status_code >= 300:
            raise TwentyError(f"HTTP_{response.status_code}", response.status_code)
        try:
            body = response.json()
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}

    def create_person(self, payload: dict[str, Any]) -> str:
        return _record_id(self._call("POST", "people", payload), "Person")

    def create_opportunity(self, payload: dict[str, Any]) -> str:
        return _record_id(self._call("POST", "opportunities", payload), "Opportunity")

    def opportunity_stage(self, opportunity_id: str) -> str | None:
        body = self._call("GET", f"opportunities/{quote(opportunity_id, safe='')}")
        stage = _record(body, "Opportunity").get("stage")
        return stage if isinstance(stage, str) else None

    def update_opportunity(self, opportunity_id: str, payload: dict[str, Any]) -> None:
        self._call("PATCH", f"opportunities/{quote(opportunity_id, safe='')}", payload)


def _record(body: dict[str, Any], name: str) -> dict[str, Any]:
    """O registro na resposta: data.createX, data.updateX, data.x ou data."""
    data = body.get("data") if isinstance(body.get("data"), dict) else body
    for key in (f"create{name}", f"update{name}", name[0].lower() + name[1:]):
        if isinstance(data.get(key), dict):
            return data[key]
    return data


def _record_id(body: dict[str, Any], name: str) -> str:
    record_id = _record(body, name).get("id")
    if not isinstance(record_id, str) or not record_id:
        raise TwentyError("NO_ID")
    return record_id


class TwentyLinks(SqlStore):
    """Mapa local: conversa -> Pessoa, Oportunidade e último estágio enviado."""

    SCHEMA = SCHEMA

    def __init__(self, sqlite_path: str | Path, database_url: str | None = None):
        super().__init__(sqlite_path, database_url)
        with self._connect() as (db, mark):
            db.execute(
                self._sql(
                    "INSERT INTO crm_twenty_secret (name, value) VALUES "
                    "('pseudonimo', ?) ON CONFLICT (name) DO NOTHING",
                    mark,
                ),
                [secrets.token_hex(32)],
            )
            row = db.execute(
                "SELECT value FROM crm_twenty_secret WHERE name = 'pseudonimo'"
            ).fetchone()
        self.secret = row["value"].encode("ascii")

    def all(self) -> dict[str, dict[str, Any]]:
        with self._connect() as (db, _):
            rows = db.execute("SELECT * FROM crm_twenty_links").fetchall()
        return {row["conv_key"]: dict(row) for row in rows}

    def get(self, conv_key: str) -> dict[str, Any]:
        with self._connect() as (db, mark):
            row = db.execute(
                self._sql("SELECT * FROM crm_twenty_links WHERE conv_key = ?", mark),
                [conv_key],
            ).fetchone()
        return dict(row) if row else {}

    def claim(self, conv_key: str) -> bool:
        """Reserva a conversa para um só sync por vez (dois workers, um card)."""
        now = _now()
        with self._connect() as (db, mark):
            db.execute(
                self._sql(
                    "INSERT INTO crm_twenty_links (conv_key, updated_at) VALUES (?, ?) "
                    "ON CONFLICT (conv_key) DO NOTHING",
                    mark,
                ),
                [conv_key, now.isoformat()],
            )
            cursor = db.execute(
                self._sql(
                    "UPDATE crm_twenty_links SET claimed_at = ? WHERE conv_key = ? "
                    "AND (claimed_at IS NULL OR claimed_at < ?)",
                    mark,
                ),
                [now.isoformat(), conv_key, (now - CLAIM_TTL).isoformat()],
            )
            return cursor.rowcount == 1

    def save(self, conv_key: str, *, release: bool = False, **fields: Any) -> None:
        if set(fields) - _LINK_FIELDS:
            raise ValueError("campo_nao_atualizavel")
        columns = [f"{name} = ?" for name in fields] + ["updated_at = ?"]
        if release:
            columns.append("claimed_at = NULL")
        with self._connect() as (db, mark):
            db.execute(
                self._sql(
                    f"UPDATE crm_twenty_links SET {', '.join(columns)} "
                    "WHERE conv_key = ?",
                    mark,
                ),
                [*fields.values(), _now().isoformat(), conv_key],
            )


def funnel_step(conversation: dict[str, Any], events: list[dict[str, Any]]) -> str:
    """Etapa do funil a partir da conversa e dos eventos do motor."""
    names = {event.get("event") for event in events}
    intents = {event.get("intent") for event in events}
    if conversation.get("paid") or "ORDER_PAID" in names:
        return "pago"
    if (
        conversation.get("order_id")
        or "ORDER_DRAFTED" in names
        or any(
            event.get("event") == "PAYMENT_LINK" and event.get("result") == "CREATED"
            for event in events
        )
    ):
        return "proposta"
    if intents & CLOSING_INTENTS:
        return "fechamento"
    if conversation.get("product_id") or intents & INTEREST_INTENTS:
        return "interesse"
    return "nova_conversa"


def _rank(stage: str | None) -> int | None:
    """Posição do estágio do Twenty no funil; None se for um estágio que não é nosso."""
    for index, step in enumerate(FUNNEL_ORDER):
        if FUNNEL_STAGES[step] == stage:
            return index
    return None


def _safe_label(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = normalize_text(value).strip()
    return text if _LABEL.fullmatch(text) and not _DIGITS.search(text) else None


def _origin(conversation: dict[str, Any]) -> str | None:
    """Rótulo como instagram/stories, da utm ou do Direct; nada de texto livre."""
    try:
        origin = json.loads(conversation.get("origin_json") or "{}")
    except ValueError:
        return None
    if not isinstance(origin, dict):
        return None
    utm = origin.get("utm") if isinstance(origin.get("utm"), dict) else {}
    parts = (
        utm.get("utm_source") or origin.get("source"),
        utm.get("utm_medium") or origin.get("medium"),
    )
    labels = [label for label in map(_safe_label, parts) if label]
    return "/".join(labels) or None


def _clean(text: str) -> str:
    """Última barreira: sem telefone, e-mail, CPF, CNPJ nem CEP (``summarize``)."""
    return summarize(text, limit=1)


def _amount(product: dict[str, Any] | None) -> int | None:
    price = public_price(product) if product else None
    return int(Decimal(price) * MICROS) if price else None


def _products() -> dict[str, dict[str, Any]]:
    try:
        return {item["id"]: item for item in load_catalog()}
    except (OSError, ValueError, TypeError, KeyError):
        return {}


def _money(amount: int) -> dict[str, Any]:
    return {"amountMicros": amount, "currencyCode": CURRENCY}


class TwentySync:
    """Leva as conversas do motor ao funil do Twenty, só para a frente."""

    def __init__(
        self,
        store_path_or_url: str | Path,
        client: TwentyClient | None,
        *,
        enabled: bool | None = None,
    ) -> None:
        self._target = str(store_path_or_url)
        self.client = client
        self._enabled = enabled
        self._links: TwentyLinks | None = None

    @classmethod
    def from_env(cls) -> TwentySync:
        from .config import AgentSettings

        settings = AgentSettings.from_env()
        client = TwentyClient(
            os.getenv("TWENTY_API_URL", ""), os.getenv("TWENTY_API_KEY", "")
        )
        return cls(settings.database_url or settings.operations_db, client)

    @property
    def enabled(self) -> bool:
        if self._enabled is None:
            return env_flag("TWENTY_SYNC_ENABLED")
        return self._enabled

    @property
    def links(self) -> TwentyLinks:
        if self._links is None:
            is_url = self._target.startswith(("postgres://", "postgresql://"))
            self._links = TwentyLinks(self._target, self._target if is_url else None)
        return self._links

    def plan(self, engine: Any) -> list[dict[str, Any]]:
        """O que o sync faria, conversa por conversa (sem rede)."""
        store = engine.store
        events: dict[str, list[dict[str, Any]]] = {}
        for event in store.events():
            if event.get("conv_key"):
                events.setdefault(event["conv_key"], []).append(event)
        products, links = _products(), self.links.all()
        return [
            self._operation(
                row,
                events.get(row["conv_key"], []),
                links.get(row["conv_key"], {}),
                products,
            )
            for row in store.conversations()
        ]

    def _operation(
        self,
        row: dict[str, Any],
        events: list[dict[str, Any]],
        link: dict[str, Any],
        products: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        step = funnel_step(row, events)
        rank = FUNNEL_ORDER.index(step)
        product = products.get(row.get("product_id") or "")
        amount = _amount(product)
        # Só letras: id curto não parece telefone, CEP ou documento.
        short = (
            hmac.new(self.links.secret, row["conv_key"].encode("utf-8"), hashlib.sha256)
            .hexdigest()[:8]
            .translate(_LETTERS)
        )
        lead = f"{CHANNEL_LABELS.get(row['channel'], 'Canal')} {short}"
        operation = {
            "conv_key": row["conv_key"],
            "lead": lead,
            "step": step,
            "rank": rank,
            "stage": FUNNEL_STAGES[step],
            "amount_micros": amount,
        }
        if not link.get("opportunity_id"):
            origin = _origin(row)
            name = " · ".join(
                part
                for part in (
                    product["name"] if product else None,
                    lead,
                    f"via {origin}" if origin else None,
                )
                if part
            )
            opportunity: dict[str, Any] = {
                "name": _clean(name),
                "stage": FUNNEL_STAGES[step],
            }
            if amount:
                opportunity["amount"] = _money(amount)
            person = {"name": {"firstName": "Cliente", "lastName": _clean(lead)}}
            return {
                **operation,
                "action": "CREATE",
                "person": None if link.get("person_id") else person,
                "opportunity": opportunity,
            }
        last_rank = int(link.get("last_rank", -1))
        moved = rank > last_rank
        repriced = amount is not None and amount != link.get("amount_micros")
        if link.get("last_stage") == REMOVED or not (moved or repriced):
            return {**operation, "action": "UNCHANGED"}
        target = FUNNEL_STAGES[FUNNEL_ORDER[max(rank, last_rank)]]
        patch: dict[str, Any] = {"stage": target}
        if amount:
            patch["amount"] = _money(amount)
        return {
            **operation,
            "rank": max(rank, last_rank),
            "stage": target,
            "action": "UPDATE",
            "opportunity_id": link["opportunity_id"],
            "patch": patch,
        }

    def preview(self, engine: Any) -> dict[str, Any]:
        """Para o painel: ligado ou não, e a prévia; nunca levanta erro."""
        try:
            operations = self.plan(engine)
        except Exception as exc:  # noqa: BLE001 - a prévia não derruba o painel
            return {"status": "ERROR", "error": type(exc).__name__}
        configured = bool(self.client and self.client.configured)
        return {
            "status": "ENABLED" if self.enabled else "DISABLED",
            "configured": configured,
            "preview": operations,
        }

    def sync(self, engine: Any, *, max_writes: int = MAX_WRITES) -> dict[str, Any]:
        """Cria e atualiza no Twenty; desligado ou sem chave, só a prévia."""
        try:
            operations = self.plan(engine)
        except Exception as exc:  # noqa: BLE001 - o sync não derruba quem chamou
            return {"status": "ERROR", "error": type(exc).__name__}
        if not self.enabled:
            return {"status": "DISABLED", "preview": operations}
        if self.client is None or not self.client.configured:
            return {"status": "NOT_CONFIGURED", "preview": operations}
        counts = dict.fromkeys(
            ("created", "updated", "unchanged", "failed", "deferred"), 0
        )
        writes = 0
        for operation in operations:
            if operation["action"] == "UNCHANGED":
                counts["unchanged"] += 1
                continue
            key = operation["conv_key"]
            try:
                if writes >= max_writes or not self.links.claim(key):
                    counts["deferred"] += 1
                    continue
                writes += 1
                counts[self._apply(self.client, operation)] += 1
            except Exception:  # noqa: BLE001 - uma conversa não para as outras
                counts["failed"] += 1
                # Se nem soltar a reserva der, ela vence sozinha (CLAIM_TTL).
                with suppress(Exception):
                    self.links.save(key, release=True)
        return {"status": "PARTIAL" if counts["failed"] else "OK", **counts}

    def _apply(self, client: TwentyClient, operation: dict[str, Any]) -> str:
        """Uma escrita com a conversa reservada; relê o mapa antes de escrever."""
        key, links = operation["conv_key"], self.links
        fresh = links.get(key)
        if operation["action"] == "CREATE":
            if fresh.get("opportunity_id"):
                links.save(key, release=True)
                return "unchanged"
            person_id = fresh.get("person_id")
            if not person_id:
                person = operation["person"] or {
                    "name": {
                        "firstName": "Cliente",
                        "lastName": _clean(operation["lead"]),
                    }
                }
                person_id = client.create_person(person)
                # Gravada já: se a Oportunidade falhar, a Pessoa não é criada de novo.
                links.save(key, person_id=person_id)
            opportunity_id = client.create_opportunity(
                {**operation["opportunity"], "pointOfContactId": person_id}
            )
            links.save(
                key,
                release=True,
                opportunity_id=opportunity_id,
                last_stage=operation["stage"],
                last_rank=operation["rank"],
                amount_micros=operation["amount_micros"],
            )
            return "created"
        if fresh.get("last_stage") == REMOVED or (
            int(fresh.get("last_rank", -1)) >= operation["rank"]
            and fresh.get("amount_micros") == operation["amount_micros"]
        ):
            links.save(key, release=True)
            return "unchanged"
        patch, stage, rank = dict(operation["patch"]), operation["stage"], None
        try:
            remote = client.opportunity_stage(operation["opportunity_id"])
            remote_rank = _rank(remote)
            if remote is not None and (
                remote_rank is None or remote_rank >= operation["rank"]
            ):
                # O card já está à frente (ou num estágio do fundador): fica lá.
                del patch["stage"]
                stage, rank = remote, remote_rank
                if fresh.get("amount_micros") == operation["amount_micros"]:
                    patch.pop("amount", None)
            if patch:
                client.update_opportunity(operation["opportunity_id"], patch)
        except TwentyError as exc:
            if exc.status != 404:
                raise
            links.save(key, release=True, last_stage=REMOVED)
            return "unchanged"
        links.save(
            key,
            release=True,
            last_stage=stage,
            last_rank=operation["rank"] if rank is None else rank,
            amount_micros=operation["amount_micros"],
        )
        return "updated" if patch else "unchanged"
