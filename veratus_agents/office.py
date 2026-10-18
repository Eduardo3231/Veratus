"""Escritório virtual: cada papel do AGENT_REGISTRY como um funcionário numa mesa.

Junta o que os agentes fizeram de verdade (tarefas do runtime operacional, eventos
do motor de conversas e o Orders Ledger) e devolve, por funcionário, o status, o
que está fazendo agora, as últimas ações e os números. Só nomes internos, cargos
e contagens: nada de dado de cliente, custo, margem ou fornecedor.

Os nomes são internos, para o fundador acompanhar o time. Para o cliente, quem
fala é o Harvey, e ele se apresenta como assistente virtual.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Sao_Paulo")
# Tempo sem nova ação depois do qual o funcionário volta a "aguardando".
WORKING_WINDOW = timedelta(minutes=20)
CALL_WINDOW = timedelta(hours=2)

STAFF: tuple[dict[str, str], ...] = (
    {"id": "general-manager", "nome": "Augusto", "cargo": "Gerente geral", "sala": "diretoria"},
    {"id": "customer-supervisor", "nome": "Beatriz", "cargo": "Supervisora de atendimento", "sala": "atendimento"},
    {"id": "sales-agent", "nome": "Harvey", "cargo": "Vendas", "sala": "atendimento"},
    {"id": "aftersales-agent", "nome": "Clara", "cargo": "Pós-venda", "sala": "atendimento"},
    {"id": "distribution-supervisor", "nome": "Rafael", "cargo": "Supervisor de marketplaces", "sala": "marketplaces"},
    {"id": "meta-agent", "nome": "Otávio", "cargo": "Facebook e catálogo da Meta", "sala": "marketplaces"},
    {"id": "mercado-livre-agent", "nome": "Diego", "cargo": "Mercado Livre", "sala": "marketplaces"},
    {"id": "shopee-agent", "nome": "Yumi", "cargo": "Shopee", "sala": "marketplaces"},
    {"id": "tiktok-shop-agent", "nome": "Nina", "cargo": "TikTok Shop", "sala": "marketplaces"},
    {"id": "amazon-agent", "nome": "Sérgio", "cargo": "Amazon", "sala": "marketplaces"},
    {"id": "sync-monitor-agent", "nome": "Paula", "cargo": "Estoque e sincronia", "sala": "marketplaces"},
    {"id": "social-media-worker", "nome": "Luana", "cargo": "Redes sociais", "sala": "redes"},
    {"id": "paid-acquisition-worker", "nome": "Fábio", "cargo": "Mídia paga", "sala": "redes"},
    {"id": "product-supervisor", "nome": "Helena", "cargo": "Supervisora de produto", "sala": "produto"},
    {"id": "product-intake", "nome": "Bruno", "cargo": "Cadastro de produto", "sala": "produto"},
    {"id": "copy-agent", "nome": "Lívia", "cargo": "Textos", "sala": "produto"},
    {"id": "creative-agent", "nome": "Theo", "cargo": "Fotos e criativos", "sala": "produto"},
    {"id": "pricing-agent", "nome": "Marta", "cargo": "Preços", "sala": "produto"},
    {"id": "quality-agent", "nome": "Caio", "cargo": "Qualidade", "sala": "produto"},
    {"id": "fundador", "nome": "Você", "cargo": "Fundador", "sala": "fundador"},
)  # fmt: skip

# O que cada um faz no dia, em uma frase.
MISSIONS: dict[str, str] = {
    "general-manager": "Abre e fecha o dia, distribui as ordens e consolida o relatório.",
    "customer-supervisor": "Acompanha as conversas, abre tarefas e chama você quando o cliente pede.",
    "sales-agent": "Atende no WhatsApp, Instagram e Messenger até o pagamento confirmado.",
    "aftersales-agent": "Avisa pagamento e envio e responde status e rastreio.",
    "distribution-supervisor": "Coordena os anúncios e a conexão de cada marketplace.",
    "meta-agent": "Cuida do catálogo da Meta e da loja do Facebook, o canal principal.",
    "mercado-livre-agent": "Prepara os anúncios e as respostas do Mercado Livre.",
    "shopee-agent": "Prepara os anúncios da Shopee.",
    "tiktok-shop-agent": "Prepara os anúncios da TikTok Shop.",
    "amazon-agent": "Prepara os anúncios da Amazon.",
    "sync-monitor-agent": "Compara estoque, preço e anúncios entre os canais.",
    "social-media-worker": "Monta o calendário, pede sua aprovação e publica só o aprovado.",
    "paid-acquisition-worker": "Planeja campanhas em rascunho pausado, sem gastar sem você.",
    "product-supervisor": "Coordena cadastro, textos, fotos, preço e qualidade.",
    "product-intake": "Confere a ficha de cada produto no Product Master.",
    "copy-agent": "Escreve títulos e descrições só com fatos confirmados.",
    "creative-agent": "Confere fotos e criativos, fora da quarentena.",
    "pricing-agent": "Calcula o preço por canal com as taxas de cada um.",
    "quality-agent": "Aprova ou barra o anúncio antes de ir para o canal.",
    "fundador": "Decide, aprova e entra quando o cliente pede uma pessoa.",
}

# A chave que libera o trabalho externo de cada um (todas nascem desligadas).
KEYS: dict[str, tuple[str, ...]] = {
    "sales-agent": ("CONVERSATION_MODE_WHATSAPP", "WHATSAPP_SEND_ENABLED", "PAYMENT_LINKS_ENABLED"),
    "aftersales-agent": ("WHATSAPP_SEND_ENABLED",),
    "social-media-worker": ("SOCIAL_PUBLISH_ENABLED",),
    "paid-acquisition-worker": ("PAID_MEDIA_LIVE_WRITES",),
    "meta-agent": ("META_CATALOG_FEED_ENABLED", "FACEBOOK_MESSAGING_ENABLED", "META_PUBLISH_ENABLED"),
    "mercado-livre-agent": ("MERCADO_LIVRE_ANSWERS_ENABLED", "MERCADO_LIVRE_PUBLISH_ENABLED"),
    "shopee-agent": ("SHOPEE_PUBLISH_ENABLED",),
    "tiktok-shop-agent": ("TIKTOK_SHOP_PUBLISH_ENABLED",),
    "amazon-agent": ("AMAZON_PUBLISH_ENABLED",),
}  # fmt: skip

_CHANNEL = {
    "facebook": "Facebook",
    "whatsapp": "WhatsApp",
    "instagram": "Instagram",
    "mercado_livre": "Mercado Livre",
    "shopee": "Shopee",
    "tiktok_shop": "TikTok Shop",
    "amazon": "Amazon",
    "mercado-livre": "Mercado Livre",
    "tiktok-shop": "TikTok Shop",
    "meta": "Meta",
}
_INTENT = {
    "preco": "preço",
    "disponibilidade": "disponibilidade",
    "fechamento": "fechamento",
    "dados_pedido": "dados do pedido",
    "status_pedido": "status do pedido",
    "rastreio": "rastreio",
    "objecao_preco": "achou caro",
    "hesitacao": "vou pensar",
    "consultar_alguem": "vai decidir com alguém",
    "desconto": "desconto",
    "saudacao": "saudação",
    "catalogo": "catálogo",
}
_AFTERSALES = frozenset({"status_pedido", "rastreio", "pagamento_confirmado"})
_TASK_END = frozenset(
    {"TASK_COMPLETED", "TASK_FAILED", "TASK_BLOCKED", "TASK_ESCALATED", "TASK_TIMEOUT"}
)


def _n(items: Any) -> int:
    return len(items) if isinstance(items, (list, tuple, set, dict)) else 0


def _channels(result: Mapping[str, Any]) -> str:
    names = [
        _CHANNEL.get(c, c)
        for c in result.get("channels") or result.get("checked_channels") or []
    ]
    return ", ".join(names) if names else "os canais"


def _missing(result: Mapping[str, Any], key: str = "blocked") -> str:
    reasons: dict[str, int] = {}
    for item in result.get(key) or []:
        for field in item.get("missing_fields") or []:
            reasons[field] = reasons.get(field, 0) + 1
    labels = {
        "images": "sem foto real",
        "sale_price": "sem preço",
        "cost": "sem custo no banco",
        "approved_status": "sem aprovação",
    }
    return "; ".join(
        f"{count} {labels.get(field, field)}" for field, count in reasons.items()
    )


def _connection(result: Mapping[str, Any]) -> str:
    channel = _CHANNEL.get(result.get("channel", ""), result.get("channel", "canal"))
    if result.get("credentials") == "MISSING":
        state = "sem credenciais"
    elif result.get("auth") == "VALID" or result.get("account") == "VERIFIED":
        state = "conta conectada"
    else:
        state = "conexão a conferir"
    publish = (
        "publicação bloqueada pela chave geral"
        if "BLOCKED" in str(result.get("publish"))
        else "publicação liberada"
    )
    return f"{channel}: {state}; rascunho local; {publish}"


# O que cada tarefa do runtime operacional quer dizer, em uma linha.
TASK_TEXT: dict[str, Callable[[Mapping[str, Any]], str]] = {
    "PLAN_OPERATIONAL_AUDIT": lambda r: (
        f"Abriu a revisão do dia: {r.get('products_total', 0)} produtos, sem escrita externa"
    ),
    "COORDINATE_PRODUCT_AUDIT": lambda r: (
        f"Revisão de produto: {r.get('eligible', 0)} prontos, {r.get('blocked', 0)} com pendência"
    ),
    "AUDIT_ACTIVE_PRODUCTS": lambda r: (
        f"Conferiu o cadastro: {r.get('active', 0)} produtos ativos"
    ),
    "AUDIT_PRODUCT_COPY": lambda r: (
        f"Textos: {_n(r.get('missing_description'))} sem descrição"
        if _n(r.get("missing_description"))
        else "Textos: todas as descrições prontas"
    ),
    "AUDIT_PRODUCT_ASSETS": lambda r: (
        f"Fotos: {_n(r.get('missing_assets'))} produtos sem foto real"
    ),
    "AUDIT_PRODUCT_PRICING": lambda r: (
        f"Preços: {_n(r.get('missing_pricing'))} produtos sem preço de venda"
    ),
    "AUDIT_PRODUCT_READINESS": lambda r: (
        f"Qualidade: {_n(r.get('eligible'))} prontos para anunciar ({_missing(r) or 'sem pendência'})"
    ),
    "COORDINATE_CONNECTION_AUDIT": lambda r: f"Coordenou a checagem de {_channels(r)}",
    "READ_ONLY_CONNECTION_CHECK": _connection,
    "AUDIT_SYNC_STATE": lambda r: f"Sincronia conferida em {_channels(r)}, sem escrita",
    "AUDIT_PAID_ACQUISITION_READINESS": lambda r: (
        f"Mídia paga em {r.get('mode', 'SHADOW')}: nada é gasto sem sua aprovação"
    ),
    "AUDIT_CONVERSATION_LEVELS": lambda r: (
        "Níveis dos canais: "
        + ", ".join(
            f"{_CHANNEL.get(c, c)} {m}" for c, m in (r.get("modes") or {}).items()
        )
    ),
    "AUDIT_SALES_READINESS": lambda r: (
        f"Vendas: link de pagamento {'ligado' if r.get('payment_links') == 'ENABLED' else 'desligado'}, {_n(r.get('auto_channels'))} canais em AUTO"
    ),
    "AUDIT_AFTERSALES_READINESS": lambda r: (
        "Pós-venda: avisos " + ", ".join(r.get("templates") or [])
    ),
    "AUDIT_SOCIAL_SCHEDULE": lambda r: (
        f"Agenda das redes: {'publicação ligada' if r.get('publish') == 'ENABLED' else 'só prévia, nada publica sem aprovação'}"
    ),
    "CONSOLIDATE_OPERATIONAL_AUDIT": lambda r: (
        f"Fechou o relatório: {_n(r.get('eligible_products'))} produtos prontos, {_n(r.get('next_actions'))} próximos passos"
    ),
    "CREATE_EXECUTION_PLAN": lambda r: (
        f"Plano de anúncios para {_channels(r)}: {_n(r.get('products'))} produtos aptos"
    ),
    "COORDINATE_PRODUCT_TEAM": lambda r: (
        f"Distribuiu o trabalho para {_n(r.get('delegated_agents'))} pessoas do produto"
    ),
    "VALIDATE_PRODUCT_INPUT": lambda r: (
        f"Validou a ficha de {r.get('processed', 0)} produtos"
    ),
    "BUILD_CHANNEL_COPY": lambda r: (
        f"Escreveu os textos de {r.get('processed', 0)} anúncios, {r.get('unsupported_claims', 0)} afirmações sem prova"
    ),
    "VALIDATE_CREATIVE_ASSETS": lambda r: (
        f"Conferiu {r.get('assets_verified', 0)} fotos e criativos"
    ),
    "CALCULATE_CHANNEL_PRICING": lambda r: (
        f"Calculou o preço por canal de {r.get('processed', 0)} produtos ({_missing(r) or 'sem pendência'})"
    ),
    "RUN_PRODUCT_AND_CHANNEL_QA": lambda r: (
        f"QA dos anúncios: {_n(r.get('eligible'))} aprovados ({_missing(r) or 'sem pendência'})"
    ),
    "COORDINATE_CHANNEL_DRAFTS": lambda r: f"Coordenou os rascunhos de {_channels(r)}",
    "PREPARE_CANONICAL_LISTING": lambda r: (
        f"{_CHANNEL.get(r.get('channel', ''), 'Canal')}: {_n(r.get('drafts'))} rascunhos de anúncio, publicação bloqueada pela chave"
    ),
    "SYNC_DRAFTS": lambda r: (
        f"Comparou os rascunhos de {_channels(r)}: {_n(r.get('conflicts'))} divergências"
    ),
    "CONSOLIDATE_MANAGER_REPORT": lambda r: (
        f"Relatório do plano: {_n(r.get('eligible_products'))} produtos prontos"
    ),
    "SYNC": lambda r: (
        f"Sincronizou os marketplaces: {_n(r.get('conflicts'))} divergências"
    ),
    "APPROVALS": lambda r: f"Aprovações pendentes com você: {_n(r.get('approvals'))}",
    "ERRORS": lambda r: f"Erros abertos: {_n(r.get('errors'))}",
}


def describe_task(action: str, result: Mapping[str, Any] | None) -> str:
    """Uma linha em português para a tarefa, sem dado de cliente nem custo."""
    describe = TASK_TEXT.get(action)
    if describe is None:
        return action.replace("_", " ").capitalize()
    try:
        return describe(result or {})
    except (TypeError, AttributeError, ValueError):
        return action.replace("_", " ").capitalize()


def describe_event(event: Mapping[str, Any]) -> tuple[str, str, str] | None:
    """Evento do motor de conversas -> (funcionário, texto, tom). None = não mostra."""
    name = event.get("event")
    channel = _CHANNEL.get(event.get("channel") or "", event.get("channel") or "")
    intent = event.get("intent") or ""
    who = "aftersales-agent" if intent in _AFTERSALES else "sales-agent"
    label = f" ({_INTENT[intent]})" if intent in _INTENT else ""
    result = event.get("result") or ""
    if name == "REPLY_SENT":
        return who, f"Respondeu no {channel}{label}", "neutro"
    if name == "DRAFT_QUEUED":
        return who, f"Rascunho no {channel}{label} para sua aprovação", "aviso"
    if name == "SEND_BLOCKED":
        return who, f"Envio bloqueado no {channel}", "alerta"
    if name == "ORDER_DRAFTED":
        return "sales-agent", f"Registrou um pedido no {channel}", "neutro"
    if name == "PAYMENT_LINK":
        if result == "CREATED":
            return "sales-agent", "Criou o link de pagamento", "neutro"
        return "sales-agent", "Link de pagamento espera o modo AUTO", "aviso"
    if name == "WHATSAPP_NEXT_STEP":
        return "sales-agent", "Levou a conversa do Instagram para o WhatsApp", "neutro"
    if name == "CEP_CHECKED":
        return "sales-agent", "Conferiu o CEP do pedido", "neutro"
    if name == "ORDER_PAID":
        return "sales-agent", "Pagamento confirmado pelo Mercado Pago", "ganho"
    if name == "ORDER_PAID_NOTICE":
        return "aftersales-agent", "Avisou o cliente do pagamento", "neutro"
    if name in ("HANDOFF", "HANDOFF_OFFERED"):
        text = (
            "Chamou você: o cliente pediu uma pessoa"
            if name == "HANDOFF"
            else "Ofereceu chamar o responsável"
        )
        return "customer-supervisor", text, "alerta" if name == "HANDOFF" else "neutro"
    if name == "TASK":
        return "customer-supervisor", f"Abriu uma tarefa no painel ({channel})", "aviso"
    if name in ("GATE_FAILED", "CIRCUIT_BREAKER", "DISJUNTOR"):
        return (
            "customer-supervisor",
            f"Filtro barrou uma resposta no {channel}",
            "alerta",
        )
    if name == "STATE_CHANGED" and event.get("actor") not in (None, "sales-agent"):
        text = (
            "Assumiu a conversa"
            if result == "HUMAN"
            else "Devolveu a conversa ao Harvey"
        )
        return "fundador", text, "neutro"
    return None


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _brl(value: Decimal) -> str:
    text = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {text}"


def _price(order: Mapping[str, Any]) -> Decimal:
    try:
        return Decimal(str(order.get("sale_price") or "0"))
    except InvalidOperation:
        return Decimal(0)


def activities(engine: Any = None, runtime: Any = None) -> list[dict[str, Any]]:
    """Ações de todos, na ordem do relógio: {at, ator, texto, tom, ref}."""
    items: list[dict[str, Any]] = []
    board = getattr(runtime, "tasks", None)
    tasks = getattr(board, "tasks", {})
    for record in getattr(board, "audit", []):
        if record.action not in _TASK_END or record.task_id not in tasks:
            continue
        at = _parse(record.timestamp)
        if at is None:
            continue
        task = tasks[record.task_id]
        result = task.result if isinstance(task.result, dict) else {}
        tone = "neutro" if record.action == "TASK_COMPLETED" else "alerta"
        items.append(
            {
                "at": at,
                "ator": task.agent,
                "texto": describe_task(task.action, result),
                "tom": tone,
                "ref": record.event_id,
            }
        )
    if engine is not None:
        for event in engine.store.events():
            described = describe_event(event)
            at = _parse(event.get("at"))
            if described is None or at is None:
                continue
            who, text, tone = described
            items.append(
                {
                    "at": at,
                    "ator": who,
                    "texto": text,
                    "tom": tone,
                    "ref": event.get("event_id"),
                    "evento": event.get("event"),
                }
            )
    items.sort(key=lambda item: item["at"])
    return items


def snapshot(
    engine: Any = None,
    runtime: Any = None,
    ledger: Any = None,
    *,
    now: datetime | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """O escritório agora: cada funcionário com status, ação atual e números."""
    env = os.environ if env is None else env
    now = now or datetime.now(timezone.utc)
    acts = activities(engine, runtime)
    pending_drafts = (
        sum(
            1
            for d in engine.store.queue()
            if d.get("status") in ("PENDING_APPROVAL", "SHADOW_DRAFT")
        )
        if engine is not None
        else 0
    )
    agents: dict[str, dict[str, Any]] = {}
    moves: list[dict[str, str]] = []
    for member in STAFF:
        mine = [item for item in acts if item["ator"] == member["id"]]
        last = mine[-1] if mine else None
        keys = [
            {
                "nome": key,
                "ligada": str(env.get(key, "false")).strip().lower()
                in ("true", "auto"),
            }
            for key in KEYS.get(member["id"], ())
        ]
        if last and now - last["at"] <= WORKING_WINDOW:
            status = (
                "bloqueado"
                if last["tom"] == "alerta" and member["id"] != "customer-supervisor"
                else "trabalhando"
            )
        elif member["id"] == "sales-agent" and pending_drafts:
            status = "aprovacao"
        elif keys and not mine and not any(key["ligada"] for key in keys):
            status = "desligado"
        else:
            status = "aguardando"
        agents[member["id"]] = {
            "status": status,
            "agora": last["texto"] if last else "Aguardando a próxima tarefa",
            "tag": {"texto": last["texto"], "tom": last["tom"]}
            if last and status == "trabalhando"
            else None,
            "ultimas": [
                {
                    "t": item["at"].astimezone(TZ).strftime("%H:%M"),
                    "texto": item["texto"],
                }
                for item in mine[-5:]
            ][::-1],
            "contadores": {
                "acoes_hoje": sum(
                    1
                    for item in mine
                    if item["at"].astimezone(TZ).date() == now.astimezone(TZ).date()
                )
            },
            "chaves": keys,
        }
    for item in acts:
        if item.get("evento") == "HANDOFF":
            moves.append(
                {
                    "id": str(item["ref"]),
                    "quem": "customer-supervisor",
                    "para": "fundador",
                }
            )
    orders = (
        ledger.list_orders()
        if ledger is not None and hasattr(ledger, "list_orders")
        else []
    )
    paid = [o for o in orders if o.get("status") in ("PAID", "SHIPPED", "DELIVERED")]
    per_hour = [0] * 13
    for order in paid:
        moment = _parse(order.get("updated_at") or order.get("recorded_at"))
        if moment and 8 <= moment.astimezone(TZ).hour <= 20:
            per_hour[moment.astimezone(TZ).hour - 8] += 1
    called = any(
        item.get("evento") == "HANDOFF" and now - item["at"] <= CALL_WINDOW
        for item in acts
    )
    return {
        "empresa": "Veratus",
        "gerado_em": now.isoformat(),
        "funcionarios": [
            {**member, "missao": MISSIONS.get(member["id"], "")} for member in STAFF
        ],
        "agentes": agents,
        "movimentos": moves[-20:],
        "chamado": called,
        "quadro": {
            "receita": _brl(sum((_price(o) for o in paid), Decimal(0))),
            "pedidos": len(orders),
            "pagos": len(paid),
            "enviados": sum(
                1 for o in orders if o.get("status") in ("SHIPPED", "DELIVERED")
            ),
            "por_hora": per_hour,
            "relogio": now.astimezone(TZ).strftime("%H:%M"),
        },
        "external_write": False,
    }
