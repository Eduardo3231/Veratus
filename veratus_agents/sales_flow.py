"""Mapa do fluxo de vendas: de onde vem o lead e qual papel age em cada etapa.

Dados puros, sem rede. Só entram ids que já existem em operations.AGENT_REGISTRY
(papel novo precisa da confirmação do fundador). ``flow_report`` junta o mapa com
o estado atual: nível efetivo do canal, chaves de envio e métricas.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

STAGES = ("captacao", "negociacao", "fechamento", "pagamento", "pos_venda")
STAGE_LABELS = {
    "captacao": "Captação",
    "negociacao": "Negociação",
    "fechamento": "Fechamento",
    "pagamento": "Pagamento",
    "pos_venda": "Pós-venda",
}

SALES_FLOW: tuple[dict[str, Any], ...] = (
    {
        "channel": "whatsapp",
        "label": "WhatsApp",
        "conversation_channel": "whatsapp",
        "send_flag": "WHATSAPP_SEND_ENABLED",
        "lead_sources": (
            "botão do site com a referência VT da visita",
            "anúncio click-to-WhatsApp",
            "link da bio e do Direct",
        ),
        "stages": {
            "captacao": ("paid-acquisition-worker", "social-media-worker"),
            "negociacao": ("sales-agent",),
            "fechamento": ("sales-agent",),
            "pagamento": ("sales-agent",),
            "pos_venda": ("aftersales-agent",),
        },
        "how": (
            "A IA conversa e fecha; o link é do Mercado Pago e a venda só conta "
            "com o pagamento confirmado pelo provedor."
        ),
    },
    {
        "channel": "instagram",
        "label": "Instagram Direct",
        "conversation_channel": "instagram",
        "send_flag": "INSTAGRAM_MESSAGING_ENABLED",
        "lead_sources": (
            "mensagem no Direct",
            "resposta privada a um comentário",
        ),
        "stages": {
            "captacao": ("social-media-worker", "paid-acquisition-worker"),
            "negociacao": ("sales-agent",),
            "fechamento": ("sales-agent",),
            "pagamento": ("sales-agent",),
            "pos_venda": ("aftersales-agent",),
        },
        "how": "Mesmo motor do WhatsApp, com a janela de 24 h do Direct.",
    },
    {
        "channel": "instagram_comments",
        "label": "Comentários do Instagram",
        "conversation_channel": None,
        "send_flag": "INSTAGRAM_DM_ENABLED",
        "lead_sources": ("comentário com palavra-chave num post",),
        "stages": {"captacao": ("social-media-worker",)},
        "how": (
            "Uma resposta privada por comentário, em até 7 dias, com o link do "
            "site; a conversa segue no Direct."
        ),
    },
    {
        "channel": "mercado_livre",
        "label": "Mercado Livre",
        "conversation_channel": "mercado_livre",
        "send_flag": "MERCADO_LIVRE_ANSWERS_ENABLED",
        "lead_sources": ("pergunta no anúncio", "mensagem pós-venda"),
        "stages": {
            "captacao": ("mercado-livre-agent",),
            "negociacao": ("sales-agent",),
            "fechamento": ("mercado-livre-agent",),
            "pagamento": ("sync-monitor-agent",),
            "pos_venda": ("aftersales-agent", "sync-monitor-agent"),
        },
        "how": (
            "A IA responde sem passar contato; a compra e o pagamento acontecem "
            "no próprio anúncio."
        ),
    },
    {
        "channel": "shopee",
        "label": "Shopee",
        "conversation_channel": "shopee",
        "send_flag": None,
        "lead_sources": ("pergunta no anúncio",),
        "stages": {
            "captacao": ("shopee-agent",),
            "negociacao": ("sales-agent",),
            "fechamento": ("shopee-agent",),
            "pos_venda": ("sync-monitor-agent",),
        },
        "how": "Resposta em rascunho para colar no painel: a API de mensagens não foi verificada.",
    },
    {
        "channel": "tiktok_shop",
        "label": "TikTok Shop",
        "conversation_channel": "tiktok_shop",
        "send_flag": None,
        "lead_sources": ("pergunta no anúncio",),
        "stages": {
            "captacao": ("tiktok-shop-agent",),
            "negociacao": ("sales-agent",),
            "fechamento": ("tiktok-shop-agent",),
            "pos_venda": ("sync-monitor-agent",),
        },
        "how": "Resposta em rascunho para colar no painel: a API de mensagens não foi verificada.",
    },
    {
        "channel": "amazon",
        "label": "Amazon",
        "conversation_channel": "amazon",
        "send_flag": None,
        "lead_sources": ("mensagem do comprador",),
        "stages": {
            "captacao": ("amazon-agent",),
            "negociacao": ("sales-agent",),
            "fechamento": ("amazon-agent",),
            "pos_venda": ("sync-monitor-agent",),
        },
        "how": "Resposta em rascunho para colar no painel: a API de mensagens não foi verificada.",
    },
)
# Quem cuida das passagens ao fundador e do disjuntor em todos os canais de conversa.
SUPERVISOR = "customer-supervisor"


def flow_roles() -> set[str]:
    roles = {SUPERVISOR}
    for item in SALES_FLOW:
        for ids in item["stages"].values():
            roles.update(ids)
    return roles


def _flag(env: Mapping[str, str], name: str | None) -> bool | None:
    if name is None:
        return None
    return env.get(name, "false").strip().lower() == "true"


def flow_report(
    engine: Any = None,
    orders: Any = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Mapa + estado atual. Só booleanos das chaves; nenhum segredo ou dado de cliente."""
    from .conversations import channel_mode
    from .operations import AGENT_REGISTRY

    env = os.environ if env is None else env
    metrics = engine.metrics() if engine is not None else {}
    channels = []
    for item in SALES_FLOW:
        conversation_channel = item["conversation_channel"]
        if conversation_channel is None:
            mode = None
        elif engine is not None:
            mode = engine.mode(conversation_channel)
        else:
            mode = channel_mode(conversation_channel)
        channels.append(
            {
                "channel": item["channel"],
                "label": item["label"],
                "lead_sources": list(item["lead_sources"]),
                "stages": {
                    stage: list(item["stages"][stage])
                    for stage in STAGES
                    if stage in item["stages"]
                },
                "how": item["how"],
                "mode": mode,
                "send_enabled": _flag(env, item["send_flag"]),
                "metrics": metrics.get(conversation_channel),
            }
        )
    return {
        "stages": [{"id": stage, "label": STAGE_LABELS[stage]} for stage in STAGES],
        "supervisor": SUPERVISOR,
        "autoprocess": _flag(env, "CONVERSATION_AUTOPROCESS_ENABLED"),
        "channels": channels,
        "roles": {
            role: AGENT_REGISTRY[role].mission
            for role in sorted(flow_roles())
            if role in AGENT_REGISTRY
        },
        "orders": orders.summary() if orders is not None else None,
    }
