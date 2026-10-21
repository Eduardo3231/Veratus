"""Mapa do fluxo de vendas: de onde vem o lead e qual papel age em cada etapa.

Os canais do kit de perfil (``branding/profile-kit.md``) vêm primeiro, com a
função e o próximo passo de cada um; são o foco do Harvey (``harvey.py``).

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


def _profile(channel: str) -> dict[str, str]:
    from .harvey import PROFILE_CHANNELS

    item = next(entry for entry in PROFILE_CHANNELS if entry["canal"] == channel)
    return {"funcao": item["funcao"], "proximo_passo": item["proximo_passo"]}


# Canais do kit de perfil (branding/profile-kit.md) primeiro: são o foco do Harvey.
# Depois, os marketplaces, com o Facebook (loja do catálogo da Meta) como principal.
SALES_FLOW: tuple[dict[str, Any], ...] = (
    {
        "channel": "instagram",
        "label": "Instagram Direct",
        "grupo": "perfil",
        "perfil": _profile("instagram"),
        "conversation_channel": "instagram",
        "send_flag": "INSTAGRAM_MESSAGING_ENABLED",
        "lead_sources": (
            "mensagem no Direct",
            "resposta privada a um comentário",
        ),
        "stages": {
            "captacao": ("social-media-worker", "paid-acquisition-worker"),
            "negociacao": ("sales-agent",),
        },
        "how": (
            "O Harvey conversa no Direct; quando a pessoa pede valor, "
            "disponibilidade ou pedido, responde e leva ao WhatsApp com o produto "
            "e a origem no link."
        ),
    },
    {
        "channel": "instagram_stories",
        "label": "Stories do Instagram",
        "grupo": "perfil",
        "perfil": _profile("instagram_stories"),
        "conversation_channel": "instagram",
        "send_flag": "INSTAGRAM_MESSAGING_ENABLED",
        "lead_sources": ("resposta a um Story", "enquete ou caixa de perguntas"),
        "stages": {
            "captacao": ("social-media-worker",),
            "negociacao": ("sales-agent",),
        },
        "how": (
            "Quem responde um Story cai no Direct: o Harvey responde e leva ao "
            "WhatsApp, e o link marca que a conversa veio dos Stories."
        ),
    },
    {
        "channel": "instagram_comments",
        "label": "Comentários do Instagram",
        "grupo": "perfil",
        "perfil": None,
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
        "channel": "whatsapp",
        "label": "WhatsApp",
        "grupo": "perfil",
        "perfil": _profile("whatsapp"),
        "conversation_channel": "whatsapp",
        "send_flag": "WHATSAPP_SEND_ENABLED",
        "lead_sources": (
            "botão do site com a referência VT da visita",
            "link do Harvey no Instagram e nos Stories",
            "anúncio click-to-WhatsApp",
        ),
        "stages": {
            "captacao": ("paid-acquisition-worker", "social-media-worker"),
            "negociacao": ("sales-agent",),
            "fechamento": ("sales-agent",),
            "pagamento": ("sales-agent",),
            "pos_venda": ("aftersales-agent",),
        },
        "how": (
            "Fechamento acompanhado: o Harvey fecha (dados depois do sim, link do "
            "Mercado Pago) e acompanha o pagamento confirmado pelo provedor e o envio."
        ),
    },
    {
        "channel": "tiktok",
        "label": "TikTok",
        "grupo": "perfil",
        "perfil": _profile("tiktok"),
        "conversation_channel": None,
        "send_flag": None,
        "lead_sources": (
            "vídeo vertical reaproveitado do Reels",
            "comentário no vídeo",
        ),
        "stages": {"captacao": ("social-media-worker", "paid-acquisition-worker")},
        "how": (
            "Descoberta e alcance: o vídeo leva ao perfil e ao site. Sem conversa "
            "automática; comentário se responde à mão, com rascunho das skills ig-*."
        ),
    },
    {
        # Marketplace principal. A Meta não tem API pública de anúncio no
        # Marketplace para empresas no Brasil: a vitrine é a loja do catálogo da
        # Meta, e a conversa chega no Messenger da Página.
        "channel": "facebook",
        "label": "Facebook",
        "grupo": "marketplace",
        "principal": True,
        "perfil": None,
        "conversation_channel": "facebook",
        "send_flag": "FACEBOOK_MESSAGING_ENABLED",
        "lead_sources": (
            "loja do Facebook (catálogo da Meta) com o botão de mensagem",
            "anúncio que abre conversa no Messenger",
            "mensagem direta à Página",
        ),
        "stages": {
            "captacao": (
                "meta-agent",
                "paid-acquisition-worker",
                "social-media-worker",
            ),
            "negociacao": ("sales-agent",),
            "fechamento": ("sales-agent",),
            "pagamento": ("sales-agent",),
            "pos_venda": ("aftersales-agent",),
        },
        "how": (
            "O Harvey responde no Messenger, na janela de 24 h, e fecha com o link do "
            "Mercado Pago; a venda só conta com o pagamento confirmado pelo "
            "provedor. O catálogo da Meta sai do mesmo cadastro dos anúncios, só "
            "com peças prontas."
        ),
    },
    {
        "channel": "mercado_livre",
        "label": "Mercado Livre",
        "grupo": "marketplace",
        "perfil": None,
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
        "grupo": "marketplace",
        "perfil": None,
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
        "grupo": "marketplace",
        "perfil": None,
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
        "grupo": "marketplace",
        "perfil": None,
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
                "grupo": item["grupo"],
                "principal": bool(item.get("principal")),
                "perfil": dict(item["perfil"]) if item["perfil"] else None,
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
