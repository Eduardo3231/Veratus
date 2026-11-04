"""Análise minuciosa por canal: gestão, marketing, contabilidade e vendas.

Cada canal recebe as quatro leituras a partir do que o sistema já guarda: conversas
e eventos do Harvey, o Orders Ledger, os anúncios dos marketplaces, os posts do
agendador e as taxas de ``config/channel_fees``. Só leitura: nada sai para fora.

``analisar_canal`` é pura (dados entram, relatório sai) e ``coletar`` monta os dados a
partir das lojas. O custo de cada peça (Product Master) entra só na margem agregada
do canal; o relatório nunca mostra custo por peça nem fornecedor.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any

CHANNELS = (
    "facebook",
    "whatsapp",
    "instagram",
    "mercado_livre",
    "shopee",
    "tiktok_shop",
    "amazon",
)
LABELS = {
    "facebook": "Facebook",
    "whatsapp": "WhatsApp",
    "instagram": "Instagram",
    "mercado_livre": "Mercado Livre",
    "shopee": "Shopee",
    "tiktok_shop": "TikTok Shop",
    "amazon": "Amazon",
}
# Chave do canal nos anúncios (marketplace_ops) e no agendador de redes.
LISTING_CHANNEL = {
    "facebook": "meta",
    "mercado_livre": "mercado-livre",
    "shopee": "shopee",
    "tiktok_shop": "tiktok-shop",
    "amazon": "amazon",
}
SOCIAL_CHANNEL = {"facebook": "facebook", "instagram": "instagram"}
# Chaves que decidem se o canal atua de verdade (gestão mostra cada uma).
FLAGS = {
    "facebook": ("FACEBOOK_MESSAGING_ENABLED", "FACEBOOK_CLOSE_ON_WHATSAPP", "META_PUBLISH_ENABLED"),
    "whatsapp": ("WHATSAPP_SEND_ENABLED", "WHATSAPP_TEMPLATES_ENABLED", "PAYMENT_LINKS_ENABLED"),
    "instagram": ("INSTAGRAM_MESSAGING_ENABLED", "SOCIAL_PUBLISH_ENABLED"),
    "mercado_livre": ("MERCADO_LIVRE_ANSWERS_ENABLED", "MERCADO_LIVRE_SYNC_ENABLED", "MERCADO_LIVRE_PUBLISH_ENABLED"),
    "shopee": ("SHOPEE_SYNC_ENABLED", "SHOPEE_PUBLISH_ENABLED"),
    "tiktok_shop": ("TIKTOK_SHOP_SYNC_ENABLED", "TIKTOK_SHOP_PUBLISH_ENABLED"),
    "amazon": ("AMAZON_SYNC_ENABLED", "AMAZON_PUBLISH_ENABLED"),
}  # fmt: skip
CONFIRMED = frozenset({"PAID", "SHIPPED", "DELIVERED"})
LOST = frozenset({"CANCELED", "REFUNDED"})
CENT = Decimal("0.01")


def _money(value: Decimal) -> str:
    return f"{value.quantize(CENT):.2f}"


def _on(env: Mapping[str, str], name: str) -> bool:
    return str(env.get(name, "false")).strip().lower() == "true"


def _minutes(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    try:
        delta = datetime.fromisoformat(end) - datetime.fromisoformat(start)
    except ValueError:
        return None
    return max(delta.total_seconds() / 60, 0.0)


def _fee_key(channel: str, payment_method: str) -> str:
    if channel in LISTING_CHANNEL and channel != "facebook":
        return channel
    return "direct_pix" if payment_method == "pix" else "direct_card"


def _gestao(channel: str, dados: Mapping[str, Any]) -> dict[str, Any]:
    env = dados.get("env") or {}
    chaves = {name: _on(env, name) for name in FLAGS[channel]}
    modo = env.get(f"CONVERSATION_MODE_{channel.upper()}", "SHADOW")
    anuncios = Counter(
        str(item.get("state") or item.get("status") or "?")
        for item in dados.get("anuncios", [])
    )
    eventos = Counter(item.get("event") for item in dados.get("eventos", []))
    na_fila = (
        eventos["DRAFT_QUEUED"] - eventos["DRAFT_APPROVED"] - eventos["DRAFT_REJECTED"]
    )
    pendencias = [f"chave {name} desligada" for name, on in chaves.items() if not on]
    if modo == "SHADOW":
        pendencias.append("conversas em SHADOW: o Harvey só escreve rascunho")
    if max(na_fila, 0):
        pendencias.append(f"{na_fila} resposta(s) esperando aprovação")
    if channel in LISTING_CHANNEL and not anuncios.get("PUBLISHED"):
        pendencias.append("nenhum anúncio publicado")
    return {
        "chaves": chaves,
        "modo_conversa": modo,
        "anuncios": dict(anuncios),
        "respostas_na_fila": max(na_fila, 0),
        "chamadas_ao_fundador": eventos["HANDOFF"],
        "erros_de_envio": eventos["DELIVERY_FAILED"] + eventos["ERRO_ENVIO"],
        "pendencias": pendencias,
    }


def _marketing(channel: str, dados: Mapping[str, Any]) -> dict[str, Any]:
    conversas = dados.get("conversas", [])
    origens: Counter[str] = Counter()
    for item in conversas:
        try:
            origin = json.loads(item.get("origin_json") or "{}")
        except ValueError:
            origin = {}
        origens[str(origin.get("medium") or origin.get("utm_medium") or "direto")] += 1
    social = SOCIAL_CHANNEL.get(channel)
    posts = [
        post
        for post in dados.get("posts", [])
        if social and social in (post.get("channels") or [])
    ]
    respostas = [
        minutes
        for item in conversas
        if (minutes := _minutes(item.get("first_inbound_at"), item.get("first_response_at"))) is not None
    ]  # fmt: skip
    eventos = Counter(item.get("event") for item in dados.get("eventos", []))
    return {
        "leads": len(conversas),
        "origem": dict(origens.most_common()),
        "levados_ao_whatsapp": eventos["WHATSAPP_NEXT_STEP"],
        "posts_publicados": sum(post.get("status") == "PUBLISHED" for post in posts),
        "posts_na_fila": sum(post.get("status") in ("PENDING_APPROVAL", "APPROVED", "SCHEDULED") for post in posts),
        "taxa_de_resposta": round(len(respostas) / len(conversas), 3) if conversas else None,
        "primeira_resposta_min": round(sum(respostas) / len(respostas), 1) if respostas else None,
    }  # fmt: skip


def _vendas(channel: str, dados: Mapping[str, Any]) -> dict[str, Any]:
    conversas = dados.get("conversas", [])
    pedidos = dados.get("pedidos", [])
    pagos = [order for order in pedidos if order.get("status") in CONFIRMED]
    receita = sum(
        (Decimal(str(order["net_merchandise_brl"])) for order in pagos), Decimal(0)
    )
    motivos = Counter(
        item.get("handoff_reason") for item in conversas if item.get("handoff_reason")
    )
    return {
        "conversas": len(conversas),
        "pedidos": len(pedidos),
        "pagos": len(pagos),
        "pendentes": sum(order.get("status") == "PENDING" for order in pedidos),
        "perdidos": sum(order.get("status") in LOST for order in pedidos),
        "conversao": round(len(pagos) / len(conversas), 3) if conversas else None,
        "ticket_medio": _money(receita / len(pagos)) if pagos else None,
        "pediram_desconto": sum(int(item.get("discount_requests") or 0) > 0 for item in conversas),
        "barradas_pelo_filtro": sum(int(item.get("gate_failures") or 0) for item in conversas),
        "motivos_de_chamada": dict(motivos),
    }  # fmt: skip


def _contabilidade(channel: str, dados: Mapping[str, Any]) -> dict[str, Any]:
    from .channel_economics import evaluate

    custos: Mapping[str, Decimal] = dados.get("custos") or {}
    tipos: Mapping[str, str] = dados.get("tipos") or {}
    bruto = taxas = frete = outros = custo = Decimal(0)
    sem_custo = 0
    for order in dados.get("pedidos", []):
        if order.get("status") not in CONFIRMED:
            continue
        price = Decimal(str(order["net_merchandise_brl"]))
        sku = str(order.get("sku") or "")
        result = evaluate(
            _fee_key(channel, str(order.get("payment_method") or "")),
            tipos.get(sku, "watch"),
            price,
            require_cost=False,
        )
        bruto += price
        taxas += Decimal(result["fees"])
        frete += Decimal(result["shipping"])
        outros += Decimal(result["other"])
        if sku in custos:
            custo += custos[sku]
        else:
            sem_custo += 1
    perdido = sum(
        (Decimal(str(order["net_merchandise_brl"])) for order in dados.get("pedidos", []) if order.get("status") in LOST),
        Decimal(0),
    )  # fmt: skip
    liquido = bruto - taxas - frete - outros
    margem = None
    if bruto and not sem_custo:
        margem = round(float((liquido - custo) / bruto), 4)
    return {
        "receita_confirmada": _money(bruto),
        "taxas_estimadas": _money(taxas),
        "frete_pago_pela_loja": _money(frete),
        "embalagem_e_outros": _money(outros),
        "liquido_antes_do_custo": _money(liquido),
        "margem": margem,
        "pedidos_sem_custo": sem_custo,
        "cancelado_ou_reembolsado": _money(perdido),
        "fonte_das_taxas": "config/channel_fees (valores a confirmar no painel de cada canal)",
    }


def _recomendacoes(relatorio: Mapping[str, Any]) -> list[str]:
    gestao, marketing = relatorio["gestao"], relatorio["marketing"]
    vendas, contas = relatorio["vendas"], relatorio["contabilidade"]
    out = []
    if not any(gestao["chaves"].values()):
        out.append(
            "Gestão: o canal está todo desligado; ligar as chaves na ordem do runbook."
        )
    if gestao["respostas_na_fila"]:
        out.append("Gestão: aprovar ou ajustar as respostas na fila do Harvey.")
    if not marketing["leads"]:
        out.append(
            "Marketing: sem leads; o canal precisa de post, anúncio ou catálogo ativo."
        )
    if vendas["conversas"] and not vendas["pagos"]:
        out.append(
            "Vendas: conversas sem venda confirmada; revisar o fechamento e o link de pagamento."
        )
    if vendas["pediram_desconto"]:
        out.append(
            "Vendas: clientes pedindo desconto; reforçar o que já está incluído (frete grátis, 7 dias)."
        )
    if contas["pedidos_sem_custo"]:
        out.append(
            f"Contabilidade: cadastrar o custo de {contas['pedidos_sem_custo']} pedido(s) no Product Master para fechar a margem."
        )
    return out  # fmt: skip


def analisar_canal(channel: str, dados: Mapping[str, Any]) -> dict[str, Any]:
    """As quatro leituras de um canal e o que fazer a seguir."""
    if channel not in CHANNELS:
        raise ValueError(f"canal_desconhecido:{channel}")
    relatorio = {
        "canal": channel,
        "nome": LABELS[channel],
        "gestao": _gestao(channel, dados),
        "marketing": _marketing(channel, dados),
        "vendas": _vendas(channel, dados),
        "contabilidade": _contabilidade(channel, dados),
    }
    relatorio["recomendacoes"] = _recomendacoes(relatorio)
    return relatorio


def coletar(
    *,
    conversation_store: Any = None,
    ledger: Any = None,
    marketplace_store: Any = None,
    social_store: Any = None,
    products: Callable[[], list[dict[str, Any]]] | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Monta os dados de cada canal a partir das lojas que existirem (só leitura)."""
    from .channel_economics import product_type_of
    from .orders import OrderChannel

    custos: dict[str, Decimal] = {}
    tipos: dict[str, str] = {}
    for product in (products() if products else []) or []:
        sku = str(product.get("sku") or "")
        tipos[sku] = product_type_of(product)
        raw = product.get("cost_brl") or (product.get("fields") or {}).get("cost", {})
        raw = raw.get("value") if isinstance(raw, dict) else raw
        if raw not in (None, ""):
            custos[sku] = Decimal(str(raw))
    posts = []
    if social_store is not None:
        for post in social_store.items():
            item = dict(post)
            channels = item.get("channels") or item.get("channels_json") or []
            item["channels"] = (
                json.loads(channels) if isinstance(channels, str) else channels
            )
            posts.append(item)
    env = dict(env if env is not None else os.environ)
    dados = {}
    for channel in CHANNELS:
        listing = LISTING_CHANNEL.get(channel)
        dados[channel] = {
            "env": env,
            "custos": custos,
            "tipos": tipos,
            "posts": posts,
            "conversas": conversation_store.conversations(channel=channel) if conversation_store else [],
            "eventos": conversation_store.events(channel=channel) if conversation_store else [],
            "pedidos": ledger.list_orders(channel=OrderChannel(channel), limit=1000) if ledger else [],
            "anuncios": marketplace_store.listings(listing) if marketplace_store and listing else [],
        }  # fmt: skip
    return dados


def analisar_todos(dados: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """O relatório de todos os canais e o total da empresa."""
    canais = [analisar_canal(channel, dados.get(channel, {})) for channel in CHANNELS]
    total = sum(
        (Decimal(item["contabilidade"]["receita_confirmada"]) for item in canais),
        Decimal(0),
    )
    return {
        "canais": canais,
        "total": {
            "leads": sum(item["marketing"]["leads"] for item in canais),
            "pedidos": sum(item["vendas"]["pedidos"] for item in canais),
            "pagos": sum(item["vendas"]["pagos"] for item in canais),
            "receita_confirmada": _money(total),
            "chamadas_ao_fundador": sum(item["gestao"]["chamadas_ao_fundador"] for item in canais),
        },
    }  # fmt: skip
