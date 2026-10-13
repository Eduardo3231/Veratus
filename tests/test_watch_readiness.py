"""Relógios prontos para o teste (Fase 2): gate, economia e bloqueios."""

from __future__ import annotations

import pytest

from veratus_agents import commercial_config as config
from veratus_agents.policy import hard_review_customer_reply


@pytest.mark.parametrize(
    "text",
    [
        "É inspirado em um modelo famoso.",
        "Tem movimento automático.",
        "O mecanismo é quartzo japonês.",
        "Tem resistência à água de 5 ATM.",
        "Pode mergulhar com ele.",
        "A caixa tem 41 mm de diâmetro.",
        "Vem com garantia de fábrica.",
        "É importado e não paga taxa de importação.",
        "Sai da China em dois dias.",
        "Não é taxado na alfândega.",
        "É réplica primeira linha.",
        "Parece um Submariner.",
    ],
)
def test_gate_blocks_unconfirmed_watch_claims(text):
    assert hard_review_customer_reply(text, product_collection="watches")


@pytest.mark.parametrize(
    "text",
    [
        "É banhado a ouro 18k.",
        "Colar de prata 925.",
        "Semijoia com zircônia.",
        "É hipoalergênico e não escurece.",
    ],
)
def test_gate_keeps_jewelry_to_golden_or_silver_tone(text):
    assert "material não validado" in hard_review_customer_reply(
        text, product_collection="feminine"
    )


@pytest.mark.parametrize(
    "text",
    [
        "Só hoje, corre!",
        "Promoção relâmpago por tempo limitado.",
        "Faço um desconto de 10% pra você.",
        "Use o cupom VERATUS10.",
    ],
)
def test_gate_blocks_pressure_and_discounts(text):
    assert hard_review_customer_reply(text)


@pytest.mark.parametrize(
    "text",
    [
        "O Ocean Blue sai por R$ 289,90, com frete grátis e entrega em até 7 dias.",
        "O colar tem tom dourado e acabamento delicado.",
        "A equipe confirma a garantia e as especificações por aqui.",
        "Quer que eu siga com o pedido do Black GMT?",
    ],
)
def test_gate_still_lets_confirmed_facts_through(text):
    assert hard_review_customer_reply(text, product_collection=None) == []


def test_watch_economics_comes_from_the_channel_engine():
    snapshot = config.watch_economics_snapshot()
    fields = snapshot["fields"]

    assert snapshot["engine"] == "channel_economics"
    assert fields["price"]["value"] == "289.90"
    assert fields["shipping_cost_paid_by_veratus"] == {
        "value": "25.00",
        "status": "ASSUMPTION",
    }
    # Pagamento e tributo continuam sem fonte: nada de COMPLETE nem CPA final.
    assert snapshot["status"] != "COMPLETE"
    assert fields["payment_fee"]["status"] == "UNVERIFIED"


def test_watch_marketplace_snapshot_is_blocked_by_origin():
    assert config.origin_confirmed() is False
    snapshot = config.watch_economics_snapshot("mercado_livre")
    assert snapshot["status"] == "BLOCKED_BY_ORIGIN"


def test_watch_goes_up_and_each_channel_lists_what_is_missing():
    watch = {"id": "ocean-blue", "category": "accessories", "subcategory": "watches"}
    photographed = {**watch, "image_status": "REAL_PHOTO"}

    # À venda no site e nas conversas, sem esperar nada.
    for channel in ("site", "whatsapp", "instagram_direct"):
        assert config.watch_channel_blockers(watch, channel) == []
    # Post e anúncio esperam só a foto real.
    assert config.watch_channel_blockers(watch, "meta") == ["NEEDS_REAL_PHOTO"]
    assert config.watch_channel_blockers(photographed, "meta") == []
    # Marketplace espera foto, envio do Brasil e os campos da ficha.
    shopee = config.watch_channel_blockers(watch, "shopee")
    assert shopee[:2] == ["NEEDS_REAL_PHOTO", "ORIGIN_UNCONFIRMED"]
    assert "FICHA:ocean-blue.cor_mostrador" in shopee
    amazon = config.watch_channel_blockers(photographed, "amazon")
    assert "FICHA:ocean-blue.gtin" in amazon
    assert "FICHA:ocean-blue.gtin" not in config.watch_channel_blockers(
        photographed, "shopee"
    )
    assert config.watch_channel_blockers({"category": "jewelry_accessories"}) == []


def test_filled_sheet_releases_marketplaces_and_economics(filled_watch_sheet):
    photographed = {
        "id": "ocean-blue",
        "category": "accessories",
        "subcategory": "watches",
        "image_status": "REAL_PHOTO",
    }

    assert config.origin_confirmed() is True
    for channel in ("mercado-livre", "shopee", "tiktok-shop", "amazon"):
        assert config.watch_channel_blockers(photographed, channel) == []
    assert config.watch_economics_snapshot("mercado_livre")["status"] != (
        "BLOCKED_BY_ORIGIN"
    )


def test_storefront_blocker_and_open_question_stay_in_place():
    assert config.STOREFRONT_BLOCKERS[0]["status"] == "BLOCKING"
    assert config.FOUNDER_OPEN_QUESTIONS[0]["id"] == "delivery-vs-registered-origin"
    assert config.FOUNDER_OPEN_QUESTIONS[0]["status"] == "PENDING_FOUNDER"
