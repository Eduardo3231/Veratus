"""Harvey: o agente de vendas da Veratus, no modelo do kit de perfil.

Do ethanplusai/harvey (MIT) fica o ciclo do agente: captar, conversar, fechar e
aprender. Na Veratus ele trabalha só por entrada (quem escreveu, comentou ou
respondeu um Story) e com os papéis de canal de ``branding/profile-kit.md``:

- Instagram: identidade, confiança e conversa; próximo passo, site ou WhatsApp;
- Stories: frequência e resposta; próximo passo, WhatsApp;
- WhatsApp: atendimento e conversão; fechamento acompanhado;
- TikTok: descoberta e alcance; próximo passo, perfil e site.

No Instagram (Direct e respostas aos Stories), quando a conversa pede valor,
disponibilidade ou pedido, o Harvey responde e leva ao WhatsApp com um link que
já diz o produto e a origem; lá ele fecha e acompanha pagamento e envio. A
prospecção fria do Harvey original (e-mail, LinkedIn, busca de contatos) não
existe aqui. ``report`` é o "aprender": só contagens, sem dado de cliente.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

NAME = "Harvey"
INTRO = "Olá! Aqui é o Harvey, assistente virtual da Veratus."
# O número comercial do site (landing/site.js e scripts/build_landing.py).
WHATSAPP_NUMBER = "5511958323612"
# Kit de perfil: do Instagram, o fechamento vai para o WhatsApp.
CLOSE_ON_WHATSAPP = frozenset({"instagram"})
# "Enviar ao WhatsApp apenas quando a conversa pedir valores, disponibilidade ou
# atendimento" (social/auditoria-e-estrategia.md).
WHATSAPP_NEXT_STEP_INTENTS = frozenset(
    {
        "preco",
        "disponibilidade",
        "fechamento",
        "como_pedir",
        "link_pagamento",
        "dados_pedido",
    }
)
# Papéis dos canais do kit de perfil, com o que o Harvey faz em cada um.
PROFILE_CHANNELS: tuple[dict[str, str], ...] = (
    {
        "canal": "instagram",
        "label": "Instagram",
        "funcao": "Identidade, confiança e conversa",
        "conteudo": "Reels, carrosséis, coleção e bastidores reais",
        "proximo_passo": "Site ou WhatsApp",
        "harvey": (
            "Responde no Direct; valor, disponibilidade ou pedido levam ao WhatsApp."
        ),
    },
    {
        "canal": "instagram_stories",
        "label": "Stories",
        "funcao": "Frequência e resposta",
        "conteudo": "Enquetes, comparações e disponibilidade confirmada",
        "proximo_passo": "WhatsApp",
        "harvey": "Responde quem respondeu o Story e leva ao WhatsApp.",
    },
    {
        "canal": "whatsapp",
        "label": "WhatsApp",
        "funcao": "Atendimento e conversão",
        "conteudo": "Respostas objetivas, fotos e condições verificadas",
        "proximo_passo": "Fechamento acompanhado",
        "harvey": (
            "Fecha: dados depois do sim, link do Mercado Pago, aviso de pagamento "
            "confirmado e rastreio."
        ),
    },
    {
        "canal": "tiktok",
        "label": "TikTok",
        "funcao": "Descoberta e alcance",
        "conteudo": "Vídeos verticais rápidos, detalhes e escolhas por estilo",
        "proximo_passo": "Perfil e site",
        "harvey": "Sem conversa automática: o vídeo leva ao perfil e ao site.",
    },
)
# Objeções do método Harvey e a forma de cada uma no relatório.
OBJECTIONS = {
    "objecao_preco": "achou caro",
    "desconto_insistente": "insistiu no desconto",
    "hesitacao": "vou pensar",
    "hesitacao_final": "vou pensar, de novo",
    "consultar_alguem": "vai decidir com alguém",
    "pagamento_fora_do_link": "quis pagar fora do link",
    "sem_interesse": "disse que não tem interesse",
    "pergunta_ia": "perguntou se era robô",
}
_REPLY_EVENTS = frozenset({"REPLY_SENT", "DRAFT_QUEUED", "SEND_BLOCKED", "SEND_FAILED"})


def introduce(reply: str) -> str:
    """Primeira resposta da conversa: o Harvey diz quem é (e que é virtual)."""
    rest = reply.removeprefix("Olá!").strip()
    return f"{INTRO} {rest}".strip()


def whatsapp_link(product: dict[str, Any] | None, medium: str = "direct") -> str:
    """Link do Instagram para o WhatsApp, com o produto e a origem da conversa.

    O WhatsApp lê ``produto=`` e a "Origem da visita" como faz com o botão do site
    (``whatsapp.extract_attribution``), e o Harvey continua de onde parou.
    """
    name = (product or {}).get("name")
    first = (
        f"Olá! Vim pelo Instagram da Veratus e quero fazer o pedido do {name}."
        if name
        else "Olá! Vim pelo Instagram da Veratus e quero ver os modelos."
    )
    medium = "stories" if medium == "stories" else "direct"
    lines = [
        first,
        (
            f"Origem da visita: utm_source=instagram | utm_medium={medium} | "
            "utm_campaign=harvey"
        ),
    ]
    if product:
        lines.append(f"produto={product['id']}")
    text = quote("\n".join(lines), safe="-_.!~*'()")
    return f"https://wa.me/{WHATSAPP_NUMBER}?text={text}"


def _origin(row: dict[str, Any]) -> dict[str, Any]:
    try:
        origin = json.loads(row.get("origin_json") or "{}")
    except ValueError:
        return {}
    return origin if isinstance(origin, dict) else {}


def report(engine: Any) -> dict[str, Any]:
    """O que o Harvey aprendeu: canais, Instagram → WhatsApp e objeções."""
    store = engine.store
    conversations = store.conversations()
    intents: dict[str, set[str]] = {}
    led_to_whatsapp: dict[str, str] = {}
    for event in store.events():
        key = event.get("conv_key")
        if not key:
            continue
        if event["event"] in _REPLY_EVENTS and event.get("intent"):
            intents.setdefault(key, set()).add(event["intent"])
        elif event["event"] == "WHATSAPP_NEXT_STEP":
            led_to_whatsapp.setdefault(key, event.get("reason") or "direct")

    channels: dict[str, dict[str, int]] = {}
    arrived = {"conversas": 0, "direct": 0, "stories": 0, "pedidos": 0, "pagos": 0}
    for row in conversations:
        bucket = channels.setdefault(
            row["channel"], {"conversas": 0, "pedidos": 0, "pagos": 0}
        )
        bucket["conversas"] += 1
        bucket["pedidos"] += bool(row["order_id"])
        bucket["pagos"] += bool(row["paid"])
        utm = _origin(row).get("utm") or {}
        if row["channel"] == "whatsapp" and utm.get("utm_campaign") == "harvey":
            arrived["conversas"] += 1
            medium = "stories" if utm.get("utm_medium") == "stories" else "direct"
            arrived[medium] += 1
            arrived["pedidos"] += bool(row["order_id"])
            arrived["pagos"] += bool(row["paid"])

    by_key = {row["conv_key"]: row for row in conversations}
    objections = []
    for intent, label in OBJECTIONS.items():
        keys = [key for key, seen in intents.items() if intent in seen]
        if not keys:
            continue
        rows = [by_key[key] for key in keys if key in by_key]
        objections.append(
            {
                "intencao": intent,
                "rotulo": label,
                "conversas": len(keys),
                "viraram_pedido": sum(1 for row in rows if row["order_id"]),
                "pagas": sum(1 for row in rows if row["paid"]),
            }
        )
    led = list(led_to_whatsapp.values())
    return {
        "agente": NAME,
        "canais_do_perfil": [dict(item) for item in PROFILE_CHANNELS],
        "conversas_por_canal": channels,
        "instagram_para_whatsapp": {
            "levados": len(led),
            "levados_dos_stories": sum(1 for medium in led if medium == "stories"),
            "chegaram": arrived["conversas"],
            "chegaram_dos_stories": arrived["stories"],
            "pedidos": arrived["pedidos"],
            "pagos": arrived["pagos"],
        },
        "objecoes": objections,
    }
