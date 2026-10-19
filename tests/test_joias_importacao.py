"""Correntes de revenda: preço pela regra, texto Veratus e nada do fornecedor no público."""

from __future__ import annotations

import io
import json
from decimal import Decimal

import pytest
from PIL import Image

from veratus_agents.jewelry_import import (
    PriceRule,
    build_draft,
    extract_products,
)

# Regra e custos fictícios: a regra real fica em JEWELRY_PRICE_RULE, fora do Git.
RULE = PriceRule.parse("30:3:2")


def test_price_rule_ends_in_90_and_never_inverts():
    assert RULE.price(Decimal(10)) == Decimal("29.90")
    assert RULE.price(Decimal(30)) == Decimal("89.90")
    # Logo acima do limite, a peça mais cara não sai mais barata que a do limite.
    assert RULE.price(Decimal(31)) >= RULE.price(Decimal(30))
    assert RULE.price(Decimal(100)) == Decimal("199.90")
    with pytest.raises(ValueError):
        PriceRule.parse("")


def _photo() -> bytes:
    buffer = io.BytesIO()
    image = Image.new("RGB", (800, 800), "white")
    exif = Image.Exif()
    exif[0x010F] = "camera-do-fornecedor"
    image.save(buffer, "JPEG", exif=exif)
    return buffer.getvalue()


PAGE = (
    """<html><head><script type="application/ld+json">{data}</script></head></html>"""
)
ITEM = {
    "@context": "https://schema.org",
    "@type": "Product",
    "name": "Corrente Cartier Prata 925 Mista 60cm 3mm Fecho Lagosta - Loja X",
    "sku": "FX-123",
    "url": "/produto/corrente-cartier",
    "image": ["/img/1.jpg"],
    "description": "Prata 925 banhada com detalhes dourados, 7,5g.",
    "offers": {"@type": "Offer", "price": "20.00", "priceCurrency": "BRL"},
}


def test_supplier_page_becomes_a_veratus_draft_without_the_supplier():
    page = PAGE.format(data=json.dumps({"@graph": [ITEM]}))
    [item], _ = extract_products(page, "https://fornecedor.test/correntes/")
    assert item.price == Decimal("20.00") and item.images[0].endswith("/img/1.jpg")

    draft = build_draft(item, RULE, lambda url: _photo(), sku="VRT-JWL-CHN-0001")
    assert draft.issues == []
    public = json.dumps(draft.public, ensure_ascii=False).lower()
    assert draft.public["name"] == (
        "Corrente, tom prateado com detalhes em tom dourado, ref. 0001"
    )
    # Medida só vai para o cliente depois de confirmada; fica nos atributos.
    assert draft.public["listing_attributes"]["comprimento_cm"] == 60
    assert "tom prateado com detalhes em tom dourado" in public
    assert draft.public["sale_price"] == "59.90" and draft.public["cost"] is None
    # Nada do anúncio do fornecedor no que é público.
    for word in (
        "prata",
        "925",
        "banh",
        "loja x",
        "fx-123",
        "fornecedor.test",
        "20.00",
        "cartier",
        "60 cm",
    ):
        assert word not in public
    assert (
        draft.private["cost_brl"] == "20.00"
        and draft.private["supplier_ref"] == "FX-123"
    )

    [path] = draft.images
    with Image.open(io.BytesIO(draft.images[path])) as image:
        assert image.size == (1122, 1402) and not image.getexif()
