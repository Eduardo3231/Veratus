"""Anúncios de marketplace a partir do catálogo: texto limpo e nada publicado."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.build_landing import ON_HOLD as SITE_ON_HOLD
from veratus_agents import commercial_config
from veratus_agents.catalog import load_catalog
from veratus_agents.listing_content import (
    CHANNEL_LABELS,
    CHANNELS,
    DESCRIPTION_LIMITS,
    MAX_HIGHLIGHTS,
    ON_HOLD,
    TITLE_LIMITS,
    TITLE_LIMITS_STATUS,
    UnknownChannelError,
    export_listings,
    listing_copy,
    normalize_channel,
)
from veratus_agents.marketplace_adapters import Marketplace, build_distribution_draft
from veratus_agents.policy import MARKETPLACE_CHANNELS, hard_review_customer_reply

ROOT = Path(__file__).resolve().parents[1]
PRODUCTS = load_catalog()
BY_ID = {product["id"]: product for product in PRODUCTS}

# Fora do texto do anúncio em qualquer produto: frete e prazo são do
# marketplace, e nada de link, contato, origem, urgência ou material de joia.
FORBIDDEN = re.compile(
    r"(?i)frete|entreg|\bprazo\b|dias úteis|https?://|www\.|\.com\b|@|whats|"
    r"\bsite\b|contato|telefone|e-?mail|instagram|origina|autênti|importad|"
    r"origem|réplica|primeira linha|últim[ao]s|só hoje|tempo limitado|estoque|"
    r"esgot|\bouro\b|\bprata\b|banhad|\bbanho\b|folhead|semijoia|zircônia|"
    r"hipoalerg|R\$|desconto|cupom|garantia"
)
# Relógio sem ficha confirmada: nada de material, movimento, água ou medida.
WATCH_SPECS = re.compile(
    r"(?i)\baço\b|inox|\bmetal|movimento|quartzo|automático|prova d|"
    r"resistente à água|\d+\s*mm\b|diâmetro|\batm\b"
)
BARE_TONE = re.compile(r"(?i)(?<!tom )\b(dourad\w*|pratead\w*)")
JEWEL_MATERIAL = re.compile(
    r"(?i)\b(ouro|prata|banh\w*|folhead\w*|zirc[ôo]nia|hipoalerg\w*|a[çc]o|"
    r"r[óo]dio|lat[ãa]o|cristal|pedra)\b"
)
PRIVATE_KEYS = {
    "cost",
    "cost_brl",
    "sale_price",
    "supplier_ref",
    "supplier_visibility",
    "contribution_margin_brl",
    "max_cpa_brl",
    "financial_status",
    "pricing_status",
    "price_source",
    "material_status",
    "unverified_attributes",
    "visual_description",
}


@pytest.fixture(scope="module")
def export() -> dict:
    return export_listings()


def _texts(listing: dict) -> list[str]:
    return [listing["title"], listing["description"], *listing["highlights"]]


def _codes(listing: dict) -> set[str]:
    return {blocker["code"] for blocker in listing["blockers"]}


def _keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in _keys(item)}
    if isinstance(value, list):
        return {key for item in value for key in _keys(item)}
    return set()


def test_every_product_has_a_listing_per_channel(export) -> None:
    pairs = {(item["id"], item["channel"]) for item in export["listings"]}

    assert pairs == {
        (product["id"], channel) for product in PRODUCTS for channel in CHANNELS
    }
    assert export["external_write"] is False
    assert all(item["external_write"] is False for item in export["listings"])


def test_every_text_passes_the_gate_of_its_channel(export) -> None:
    for listing in export["listings"]:
        assert listing["issues"] == [], (listing["id"], listing["channel"])
        assert listing["title"] and listing["description"]
        for text in _texts(listing):
            assert (
                hard_review_customer_reply(
                    text,
                    product_collection=listing["collection"],
                    channel=listing["channel"],
                )
                == []
            ), (listing["id"], listing["channel"], text)


def test_titles_fit_the_channel_and_say_what_the_buyer_searches(export) -> None:
    assert TITLE_LIMITS == {
        "facebook": 200,
        "mercado_livre": 60,
        "shopee": 120,
        "amazon": 200,
        "tiktok_shop": 255,
    }
    assert TITLE_LIMITS_STATUS == "ASSUMPTION"
    by_key = {(item["id"], item["channel"]): item for item in export["listings"]}
    for listing in export["listings"]:
        assert len(listing["title"]) <= TITLE_LIMITS[listing["channel"]]
        assert listing["name"] in listing["title"]
        assert "Veratus" in listing["title"]

    # Sem cor confirmada na ficha, o título não diz cor de mostrador.
    assert (
        by_key[("ocean-blue", "mercado_livre")]["title"]
        == "Relógio Veratus Ocean Blue Esportivo"
    )
    assert (
        by_key[("ocean-blue", "amazon")]["title"]
        == "Veratus Relógio Ocean Blue, Esportivo e Versátil"
    )
    assert (
        by_key[("ocean-blue", "facebook")]["title"]
        == "Relógio Veratus Ocean Blue Esportivo e Versátil"
    )
    assert (
        by_key[("halo-verde", "shopee")]["title"]
        == "Colar Feminino Veratus Halo Verde em Tom Prateado com Elemento Verde e Halo"
    )
    assert (
        by_key[("lumiere", "mercado_livre")]["title"]
        == "Pulseira Feminina Veratus Lumière em Tom Dourado"
    )


def test_a_long_title_is_cut_to_the_limit() -> None:
    product = {
        **BY_ID["ocean-blue"],
        "id": "modelo-ficticio",
        "name": "Modelo Fictício Com Um Nome Comprido Demais Para o Título",
    }

    for channel in CHANNELS:
        title = listing_copy(product, channel)["title"]
        assert len(title) <= TITLE_LIMITS[channel]
        assert not title.endswith((" ", ","))


def test_description_is_short_paragraphs_with_a_short_list_of_highlights(
    export,
) -> None:
    for listing in export["listings"]:
        paragraphs = listing["description"].split("\n\n")
        assert len(paragraphs) >= 3
        assert all(0 < len(paragraph) <= 320 for paragraph in paragraphs)
        assert 3 <= len(listing["highlights"]) <= MAX_HIGHLIGHTS
        if listing["channel"] in ("amazon", "facebook"):
            assert "pergunta" not in listing["description"]
            assert "chat" not in listing["description"]


def test_forbidden_words_are_absent(export) -> None:
    for listing in export["listings"]:
        for text in _texts(listing):
            assert not FORBIDDEN.search(text), (listing["id"], text)
            if listing["collection"] == "watches":
                assert not WATCH_SPECS.search(text), (listing["id"], text)


def test_unconfirmed_material_in_the_catalog_text_stays_out(export) -> None:
    assert "aço" in BY_ID["black-gmt"]["description"]

    for listing in export["listings"]:
        if listing["id"] == "black-gmt":
            assert "aço" not in listing["description"].casefold()
            assert listing["description"].startswith(
                "O relógio Veratus Black GMT tem estilo esportivo e marcante."
            )


def test_jewelry_only_names_the_tone(export) -> None:
    jewelry = [item for item in export["listings"] if item["collection"] == "feminine"]

    assert len(jewelry) == 9 * len(CHANNELS)
    for listing in jewelry:
        assert listing["attributes"]["tom"] in ("tom dourado", "tom prateado")
        for text in _texts(listing):
            assert not BARE_TONE.search(text), (listing["id"], text)
            assert not JEWEL_MATERIAL.search(text), (listing["id"], text)


def test_watches_wait_for_origin_and_real_photo(export) -> None:
    watches = [item for item in export["listings"] if item["collection"] == "watches"]

    assert len(watches) == 9 * len(CHANNELS)
    for listing in watches:
        codes = _codes(listing)
        assert "NEEDS_REAL_PHOTO" in codes
        # O catálogo da Meta leva ao site, onde o relógio já está à venda: só
        # os marketplaces exigem envio de dentro do Brasil.
        assert ("ORIGIN_UNCONFIRMED" in codes) is (listing["channel"] != "facebook")
        # O criativo de vitrine está na quarentena: não vai para o anúncio.
        assert "MEDIA_IN_QUARANTINE" in codes
        assert listing["photos"] == []
        assert listing["price_brl"] == "289.90"
        assert listing["ready"] is False
        assert all(
            blocker["text"] and blocker["text"] != blocker["code"]
            for blocker in listing["blockers"]
        )


def test_unpriced_draft_jewelry_is_blocked_but_keeps_its_photo(export) -> None:
    for listing in export["listings"]:
        if listing["collection"] != "feminine":
            continue
        assert {"PRODUCT_DRAFT", "NEEDS_PRICING", "PRICE_MISSING"} <= _codes(listing)
        assert listing["price_brl"] is None
        assert listing["photos"] == [BY_ID[listing["id"]]["primary_image"]]


def test_noir_clover_waits_on_hold_like_the_site(export) -> None:
    assert ON_HOLD == SITE_ON_HOLD == {"noir-clover"}

    for listing in export["listings"]:
        on_hold = "ON_HOLD" in _codes(listing)
        assert on_hold is (listing["id"] == "noir-clover"), listing["id"]


def test_no_private_field_reaches_the_export(export) -> None:
    text = json.dumps(export, ensure_ascii=False).casefold()

    assert not (_keys(export) & PRIVATE_KEYS)
    for word in ("custo", "margem", "fornecedor", "supplier", "cost", "margin"):
        assert word not in text, word


def test_private_fields_of_a_product_never_reach_the_listing() -> None:
    # Valores fictícios: o anúncio só lê os campos públicos do catálogo.
    product = {
        **BY_ID["halo-verde"],
        "cost": "12.34",
        "supplier_ref": "ref-ficticia-xyz",
        "supplier_name": "Empresa Fictícia Ltda",
        "contribution_margin_brl": "45.67",
        "sale_price": "199.90",
        "price_source": "teste",
    }

    for channel in CHANNELS:
        listing = listing_copy(product, channel)
        text = json.dumps(listing, ensure_ascii=False).casefold()
        for secret in ("12.34", "xyz", "fictícia ltda", "45.67", "teste"):
            assert secret not in text
        assert listing["price_brl"] == "199.90"
        assert "PRICE_MISSING" not in _codes(listing)


def test_confirmed_sheet_facts_enter_the_watch_listing(watch_facts) -> None:
    for channel in CHANNELS:
        listing = listing_copy(BY_ID["ocean-blue"], channel)
        description = listing["description"]

        assert "Movimento quartzo." in description
        assert "Caixa em aço inoxidável." in description
        assert "Caixa de 41 mm de diâmetro." in description
        # Prazo em dias é condição da venda direta: no marketplace o gate barra
        # e a garantia segue só como atributo da ficha.
        warranty = "Garantia de 90 dias contra defeito de fabricação."
        assert (warranty in description) is (channel not in MARKETPLACE_CHANNELS)
        assert listing["attributes"]["ficha"]["garantia"] == (
            "90 dias contra defeito de fabricação"
        )
        assert (
            "O Ocean Blue tem mostrador em tom azul e pulseira em tom prateado."
            in description
        )
        # Envio e postagem são do marketplace, mesmo confirmados na ficha.
        assert "São Paulo" not in description
        assert "dias úteis" not in description
        assert listing["issues"] == []
        for text in _texts(listing):
            assert not hard_review_customer_reply(
                text, product_collection="watches", channel=channel
            )
        codes = _codes(listing)
        assert "ORIGIN_UNCONFIRMED" not in codes
        assert not any(code.startswith("FICHA:") for code in codes)
        assert "NEEDS_REAL_PHOTO" in codes
        assert listing["attributes"]["ficha"]["movimento"] == "quartzo"
        assert "estoque" not in listing["attributes"]["ficha"]


def _color_sheet(tmp_path, monkeypatch, models: dict[str, dict[str, tuple]]) -> None:
    """Ficha só com as cores dadas; nenhum fato de cor embutido."""
    lines = ["schema: veratus.watch_sheet/v1", "geral: {}", "modelos:"]
    for model, fields in models.items():
        lines.append(f"  {model}:")
        for name, (value, status) in fields.items():
            lines += [
                f"    {name}:",
                f"      valor: {value}",
                f"      status: {status}",
            ]
    path = tmp_path / "ficha-relogios.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setenv("WATCH_SHEET_PATH", str(path))
    for ref in [
        ref for ref in commercial_config.CONFIRMED_SALES_FACTS if "-cores" in ref
    ]:
        monkeypatch.delitem(commercial_config.CONFIRMED_SALES_FACTS, ref)


# Cor ou tom de relógio dito no texto (a paleta do site é só ilustração).
WATCH_COLOR_WORDS = re.compile(
    r"(?i)mostrador|acabamento|\btom\b|\b(azul|preto|branco|verde|marrom|cinza|"
    r"dourad\w*|pratead\w*|vermelho|roxo|amarelo)\b"
)


def test_watch_dial_and_tone_never_come_from_the_palette(tmp_path, monkeypatch) -> None:
    _color_sheet(tmp_path, monkeypatch, {})
    # Paleta fictícia: mostrador azul e metal dourado, sem nada na ficha.
    fake = {
        **BY_ID["royal-blue"],
        "palette": {"dial": "#15347a", "bezel": "#183b86", "metal": "gold"},
    }

    watches = [p for p in PRODUCTS if (p.get("collection") or "watches") == "watches"]

    assert len(watches) == 9
    for product in [*watches, fake]:
        for channel in CHANNELS:
            listing = listing_copy(product, channel)
            for text in _texts(listing):
                plain = text.replace(listing["name"], " ")
                assert not WATCH_COLOR_WORDS.search(plain), (listing["id"], text)
            attributes = listing["attributes"]
            assert not {"cor_do_mostrador", "cor_da_pulseira", "tom"} & set(attributes)


def test_watch_colors_come_only_from_the_confirmed_sheet(tmp_path, monkeypatch) -> None:
    _color_sheet(
        tmp_path,
        monkeypatch,
        {
            # Paleta do site: mostrador azul. A ficha confirma preto.
            "ocean-blue": {
                "cor_mostrador": ("Preto", "CONFIRMADO"),
                "cor_pulseira": ("Dourado", "PENDENTE"),
            },
            # Paleta do site: metal dourado. A ficha confirma pulseira prateada.
            "bronze-heritage": {
                "cor_mostrador": ("Marrom", "PENDENTE"),
                "cor_pulseira": ("Prateado", "CONFIRMADO"),
            },
        },
    )

    for channel in CHANNELS:
        ocean = listing_copy(BY_ID["ocean-blue"], channel)
        bronze = listing_copy(BY_ID["bronze-heritage"], channel)

        assert "Mostrador preto." in ocean["description"]
        assert ocean["attributes"]["cor_do_mostrador"] == "preto"
        assert "cor_da_pulseira" not in ocean["attributes"]
        assert "Pulseira em tom prateado." in bronze["description"]
        assert bronze["attributes"]["cor_da_pulseira"] == "prateado"
        assert "cor_do_mostrador" not in bronze["attributes"]
        for text in _texts(ocean) + _texts(bronze):
            assert not re.search(r"(?i)azul|dourad|marrom|acabamento", text), text
        for text in _texts(bronze):
            assert "mostrador" not in text.casefold(), text
        assert ocean["issues"] == bronze["issues"] == []

    assert (
        listing_copy(BY_ID["ocean-blue"], "mercado_livre")["title"]
        == "Relógio Veratus Ocean Blue Esportivo Mostrador Preto"
    )
    assert (
        listing_copy(BY_ID["bronze-heritage"], "facebook")["title"]
        == "Relógio Veratus Bronze Heritage Clássico e Marcante Pulseira em Tom Prateado"
    )


def test_facebook_comes_first_with_the_meta_catalog_limits(export) -> None:
    assert CHANNELS[0] == "facebook"
    assert CHANNEL_LABELS["facebook"] == "Facebook (catálogo da Meta)"
    assert DESCRIPTION_LIMITS == {"facebook": 9999}
    assert next(iter(export["summary"])) == "facebook"
    facebook = [item for item in export["listings"] if item["channel"] == "facebook"]

    assert len(facebook) == len(PRODUCTS)
    for listing in facebook:
        assert listing["title_limit"] == 200
        assert listing["description_limit"] == 9999
        assert len(listing["description"]) <= 9999
        # No gate, o Facebook não é marketplace sem contato (a venda é no site).
        for text in _texts(listing):
            assert not hard_review_customer_reply(
                text, product_collection=listing["collection"], channel=None
            )


def test_channel_names_and_unknown_channel() -> None:
    assert normalize_channel("mercado-livre") == "mercado_livre"
    assert normalize_channel(Marketplace.TIKTOK_SHOP) == "tiktok_shop"

    with pytest.raises(UnknownChannelError):
        listing_copy(PRODUCTS[0], "meta")


def _approved_product(name: str = "Royal Blue") -> dict:
    # Valores fictícios, só para passar no QA do Product Master.
    return {
        "sku": "VRT-REL-TST-0001",
        "name": name,
        "category": "Relógios",
        "status": "APPROVED",
        "material": "a confirmar",
        "supplier_ref": "ref-ficticia",
        "cost_brl": "10.00",
        "contribution_margin_brl": "20.00",
        "max_cpa_brl": "5.00",
        "price_brl": "289.90",
        "availability": "confirmar_com_equipe",
        "images": ["royal-blue.webp"],
        "variants": [],
        "evidence": [
            {"field": field, "reference": "teste"}
            for field in ("material", "cost_brl", "availability")
        ],
    }


@pytest.mark.parametrize(
    "channel", [channel for channel in Marketplace if channel is not Marketplace.META]
)
def test_distribution_draft_takes_its_text_from_listing_copy(channel) -> None:
    draft = build_distribution_draft(_approved_product(), channel)
    payload = draft["payload"]
    copy = listing_copy(BY_ID["royal-blue"], channel)

    assert payload["title"] == payload["canonical"]["title"] == copy["title"]
    assert payload["description"] == payload["canonical"]["description"]
    assert payload["description"] == copy["description"]
    assert "a confirmar" not in payload["description"]
    assert draft["external_write"] is False


def test_meta_draft_is_the_feed_item_and_outside_the_catalog_keeps_old_text() -> None:
    from veratus_agents.marketplace_adapters import DistributionDraftError
    from veratus_agents.meta_catalog import feed_row

    meta = build_distribution_draft(_approved_product(), Marketplace.META)["payload"]
    other = build_distribution_draft(
        _approved_product("Modelo Fora do Catálogo"), Marketplace.SHOPEE
    )["payload"]

    # O rascunho META é a linha do feed: mesmo texto e mesmos bloqueios; sem
    # estoque CONFIRMADO na ficha, nada de "in stock".
    row = feed_row(BY_ID["royal-blue"])
    assert (meta["title"], meta["description"]) == (row["title"], row["description"])
    assert meta["availability"] is None
    assert "AVAILABILITY_UNCONFIRMED" in meta["feed_blockers"]
    assert "Consulte disponibilidade" not in meta["description"]
    assert other["title"] == "Modelo Fora do Catálogo"
    with pytest.raises(DistributionDraftError):
        build_distribution_draft(
            _approved_product("Modelo Fora do Catálogo"), Marketplace.META
        )


def test_export_script_writes_valid_json(tmp_path) -> None:
    output = tmp_path / "dados" / "anuncios.json"

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "exportar_anuncios.py"),
            "--saida",
            str(output),
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["mode"] == "REVIEW"
    assert data["external_write"] is False
    assert len(data["listings"]) == len(PRODUCTS) * len(CHANNELS)
    assert set(data["summary"]) == set(CHANNELS)
    for item in data["summary"].values():
        assert item["ready"] + item["blocked"] == item["total"] == len(PRODUCTS)
        assert all(
            reason["code"] and reason["count"] > 0 and reason["text"]
            for reason in item["reasons"]
        )
    assert not list(tmp_path.rglob("*.parcial"))
    assert "Mercado Livre" in result.stdout
