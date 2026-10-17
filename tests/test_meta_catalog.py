"""Feed do catálogo da Meta: só linha pronta vai para o CSV e nada é publicado."""

from __future__ import annotations

import csv
import io
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from conftest import sheet_text

from integrations.meta_content_publisher import quarantined_media_paths
from veratus_agents import meta_catalog
from veratus_agents.catalog import load_catalog
from veratus_agents.listing_content import ON_HOLD
from veratus_agents.meta_catalog import (
    FEED_COLUMNS,
    catalog_report,
    feed_csv,
    feed_rows,
    site_base_url,
)
from veratus_agents.policy import hard_review_customer_reply

ROOT = Path(__file__).resolve().parents[1]
PRODUCTS = load_catalog()
BY_ID = {product["id"]: product for product in PRODUCTS}
HEADER = "id,title,description,availability,condition,price,link,image_link,brand"

# Fora de qualquer linha do feed: material de joia, urgência ou escassez falsa,
# fornecedor, custo e margem.
FORBIDDEN = re.compile(
    r"(?i)\bouro\b|\bprata\b|\bbanh|folhead|semi-?joia|zirc[ôo]nia|hipoalerg|"
    r"últim[oa]s|só hoje|\bcorra\b|imperd[íi]vel|esgot|tempo limitado|"
    r"estoque limitado|poucas unidades|fornecedor|supplier|\bcusto|\bmargem"
)
# Foto que existe no site e não está na quarentena (valor fictício de teste).
CLEAN_PHOTO = "assets/veratus-v-wheat-720.webp"


@pytest.fixture(autouse=True)
def _site(monkeypatch) -> None:
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)


def _ready_watch() -> dict:
    # Valores fictícios: relógio com foto real liberada.
    return {**BY_ID["ocean-blue"], "image_status": "REAL_PHOTO", "image": CLEAN_PHOTO}


def _codes(row: dict) -> set[str]:
    return {blocker["code"] for blocker in row["blockers"]}


def _by_id(rows: list[dict]) -> dict[str, dict]:
    return {row["id"]: row for row in rows}


def _fake_listing(monkeypatch, **fields) -> None:
    """O anúncio de verdade, com os campos trocados (texto fictício)."""
    real = meta_catalog.listing_copy

    def fake(product, channel):
        return {**real(product, channel), **fields}

    monkeypatch.setattr(meta_catalog, "listing_copy", fake)


def test_one_row_per_product_and_nothing_is_published() -> None:
    rows = feed_rows()
    report = catalog_report(rows)

    assert [row["id"] for row in rows] == [product["id"] for product in PRODUCTS]
    assert report["external_write"] is False
    assert report["mode"] == "REVIEW"
    assert report["columns"] == list(FEED_COLUMNS)
    assert report["ready"] + report["blocked"] == report["total"] == len(PRODUCTS)
    assert all(isinstance(row["ready"], bool) for row in rows)
    assert all(
        reason["code"] and reason["count"] > 0 and reason["text"]
        for reason in report["reasons"]
    )


def test_price_is_the_public_price_in_brl() -> None:
    rows = _by_id(feed_rows())
    # Valores fictícios: joia com preço de fonte real.
    priced = {**BY_ID["halo-verde"], "sale_price": "199.9", "price_source": "teste"}

    for product in PRODUCTS:
        row = rows[product["id"]]
        if product["id"] in ON_HOLD:
            continue
        if (product.get("collection") or "watches") == "watches":
            assert row["price"] == "289.90 BRL"
        else:
            assert row["price"] == ""
            assert "PRICE_MISSING" in _codes(row)
    assert feed_rows([priced])[0]["price"] == "199.90 BRL"
    for row in rows.values():
        assert row["price"] == "" or re.fullmatch(r"\d+\.\d{2} BRL", row["price"])


def test_title_and_description_fit_the_meta_limits(monkeypatch) -> None:
    long_name = {
        **BY_ID["ocean-blue"],
        "id": "modelo-ficticio",
        "name": "Modelo Fictício " + "Com Um Nome Muito Comprido " * 12,
    }

    for row in feed_rows([*PRODUCTS, long_name]):
        assert len(row["title"]) <= 200
        assert len(row["description"]) <= 9999
        assert not re.search(r"<[^>]*>", row["description"])
        assert "TITLE_TOO_LONG" not in _codes(row)

    monkeypatch.setattr(meta_catalog, "DESCRIPTION_LIMIT", 40)
    assert "DESCRIPTION_TOO_LONG" in _codes(feed_rows([BY_ID["ocean-blue"]])[0])
    monkeypatch.setattr(meta_catalog, "TITLE_LIMIT", 10)
    assert "TITLE_TOO_LONG" in _codes(feed_rows([BY_ID["ocean-blue"]])[0])


def test_unconfirmed_availability_blocks_the_export(tmp_path, monkeypatch) -> None:
    pending = sheet_text().replace(
        "estoque:\n      valor: 5\n      status: CONFIRMADO",
        "estoque:\n      valor: null\n      status: PENDENTE",
    )
    path = tmp_path / "ficha-relogios.yaml"
    path.write_text(pending, encoding="utf-8")
    monkeypatch.setenv("WATCH_SHEET_PATH", str(path))

    rows = feed_rows([*PRODUCTS, _ready_watch()])
    for row in rows:
        if "ON_HOLD" in _codes(row):
            continue
        assert row["availability"] == ""
        assert "AVAILABILITY_UNCONFIRMED" in _codes(row)
        assert row["ready"] is False
    # O relógio com foto real só espera o estoque: mesmo assim, fora do CSV.
    assert _codes(rows[-1]) == {"AVAILABILITY_UNCONFIRMED"}
    assert feed_csv(rows) == HEADER + "\n"
    assert {row["availability"] for row in rows} == {""}


def test_confirmed_stock_releases_the_row(watch_facts) -> None:
    row = feed_rows([_ready_watch()])[0]

    assert row["ready"] is True, row["blockers"]
    assert row["availability"] == "in stock"
    assert row["condition"] == "new"
    assert row["brand"] == "Veratus"
    assert row["link"] == f"{site_base_url()}/?relogio=ocean-blue"
    assert row["image_link"] == f"{site_base_url()}/{CLEAN_PHOTO}"
    assert "Movimento quartzo." in row["description"]
    assert not hard_review_customer_reply(
        row["description"], product_collection="watches"
    )


def test_out_of_stock_is_said_only_with_a_confirmed_zero(tmp_path, monkeypatch) -> None:
    path = tmp_path / "ficha-relogios.yaml"
    path.write_text(sheet_text().replace("valor: 5\n", "valor: 0\n"), encoding="utf-8")
    monkeypatch.setenv("WATCH_SHEET_PATH", str(path))

    assert feed_rows([_ready_watch()])[0]["availability"] == "out of stock"


def test_link_comes_from_the_public_site() -> None:
    landing = (ROOT / "landing" / "index.html").read_text(encoding="utf-8")
    rows = _by_id(feed_rows())

    assert f'<link rel="canonical" href="{site_base_url()}/">' in landing
    assert rows["ocean-blue"]["link"] == f"{site_base_url()}/?relogio=ocean-blue"
    assert rows["halo-verde"]["link"] == f"{site_base_url()}/#joias"
    assert 'id="joias"' in landing


def test_without_a_site_address_the_row_is_blocked(monkeypatch) -> None:
    from veratus_agents import social_scheduler

    monkeypatch.setenv("PUBLIC_BASE_URL", "http://sem-https.invalid")
    assert site_base_url() is None
    monkeypatch.setattr(social_scheduler, "PUBLIC_BASE", "")
    monkeypatch.delenv("PUBLIC_BASE_URL")

    for row in feed_rows([*PRODUCTS, _ready_watch()]):
        if "ON_HOLD" in _codes(row):
            continue
        assert row["link"] == row["image_link"] == ""
        assert "LINK_MISSING" in _codes(row)


def test_quarantined_media_is_never_used(watch_facts) -> None:
    quarantined = quarantined_media_paths()
    assets = (ROOT / "landing" / "assets").resolve()
    in_quarantine = {**_ready_watch(), "image": "assets/catalog/ocean-blue.webp"}
    traversal = {
        **_ready_watch(),
        "image": "../quarantine/third-party-marks/landing/assets/catalog/ocean-blue.webp",
    }
    rows = feed_rows([*PRODUCTS, in_quarantine, traversal])

    assert "/assets/catalog/ocean-blue.webp" in quarantined
    for row in rows:
        if not row["image_link"]:
            continue
        path = urlsplit(row["image_link"]).path
        assert path not in quarantined
        assert "quarantine" not in path
        assert (ROOT / "landing" / path.lstrip("/")).resolve().is_relative_to(assets)
        assert (ROOT / "landing" / path.lstrip("/")).is_file()
    assert rows[-2]["image_link"] == rows[-1]["image_link"] == ""
    assert "MEDIA_IN_QUARANTINE" in _codes(rows[-2])
    assert "IMAGE_NOT_FOUND" in _codes(rows[-1])
    assert not (rows[-2]["ready"] or rows[-1]["ready"])
    for row in _by_id(rows[:-2]).values():
        if row["collection"] == "watches":
            assert row["image_link"] == ""


def test_on_hold_products_are_excluded() -> None:
    assert ON_HOLD == {"noir-clover"}
    rows = feed_rows()
    csv_text = feed_csv(rows)

    for row in rows:
        if row["id"] in ON_HOLD:
            assert "ON_HOLD" in _codes(row)
            assert row["ready"] is False
            assert all(row[column] == "" for column in FEED_COLUMNS[1:])
        assert row["id"] not in ON_HOLD or row["id"] not in csv_text
    assert "Noir" not in csv_text


def test_no_forbidden_word_supplier_or_private_value_in_any_row(
    watch_facts,
) -> None:
    # Valores fictícios: o feed só lê os campos públicos do catálogo.
    secret = {
        **_ready_watch(),
        "cost": "12.34",
        "supplier_ref": "ref-ficticia-xyz",
        "supplier_name": "Empresa Fictícia Ltda",
        "contribution_margin_brl": "45.67",
    }
    rows = feed_rows([*PRODUCTS, secret])

    for row in rows:
        for column in FEED_COLUMNS:
            value = str(row[column])
            assert not FORBIDDEN.search(value), (row["id"], column, value)
            for private in ("12.34", "xyz", "fictícia ltda", "45.67"):
                assert private not in value.casefold()
    assert rows[-1]["ready"] is True


def test_supplier_in_the_text_blocks_the_row() -> None:
    # A descrição pública cita o fornecedor (valor fictício): a linha não sai.
    product = {
        **_ready_watch(),
        "description": "Feito pela Empresa Fictícia Ltda para o dia a dia.",
        "supplier_name": "Empresa Fictícia Ltda",
    }

    row = feed_rows([product])[0]
    assert "Empresa Fictícia Ltda" in row["description"]
    assert "PRIVATE_DATA" in _codes(row)
    assert feed_csv([row]) == HEADER + "\n"


@pytest.mark.parametrize(
    ("description", "code"),
    [
        ("Colar semijoia banhado a ouro, com zircônia.", "FORBIDDEN_WORDS"),
        ("Últimas unidades: corra, só hoje!", "FORBIDDEN_WORDS"),
        ("Hipoalergênico e folheado.", "FORBIDDEN_WORDS"),
        ("Fale com o fornecedor pelo e-mail loja@exemplo.invalid.", "PRIVATE_DATA"),
        ("Garantia vitalícia e resistente à água até 100 metros.", "GATE_ISSUES"),
    ],
)
def test_text_checks_block_the_row(watch_facts, monkeypatch, description, code) -> None:
    _fake_listing(monkeypatch, description=description)

    row = feed_rows([_ready_watch()])[0]
    assert code in _codes(row)
    assert row["ready"] is False
    assert feed_csv([row]) == HEADER + "\n"


def test_gate_runs_without_channel(watch_facts, monkeypatch) -> None:
    calls = []
    real = meta_catalog.hard_review_customer_reply

    def spy(text, *args, **kwargs):
        calls.append(kwargs)
        return real(text, *args, **kwargs)

    monkeypatch.setattr(meta_catalog, "hard_review_customer_reply", spy)
    feed_rows([_ready_watch()])

    assert calls
    assert all(call.get("channel") is None for call in calls)
    assert all(call["product_collection"] == "watches" for call in calls)


def test_csv_has_the_header_and_only_ready_rows(watch_facts) -> None:
    rows = feed_rows([*PRODUCTS, _ready_watch()])
    tampered = {**rows[0], "ready": True}  # bloqueios continuam na linha
    text = feed_csv([*rows, tampered])

    assert text.splitlines()[0] == HEADER
    parsed = list(csv.DictReader(io.StringIO(text)))
    assert [row["id"] for row in parsed] == ["ocean-blue"]
    assert parsed[0]["price"] == "289.90 BRL"
    assert parsed[0]["description"] == rows[-1]["description"]
    assert set(parsed[0]) == set(FEED_COLUMNS)
    text.encode("utf-8")
    assert sum(row["ready"] for row in rows) == 1


def test_export_script_writes_csv_and_report(tmp_path) -> None:
    output = tmp_path / "dados" / "catalogo.csv"
    report = tmp_path / "dados" / "catalogo.json"

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "exportar_catalogo_meta.py"),
            "--saida",
            str(output),
            "--json",
            str(report),
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    assert output.read_text(encoding="utf-8").splitlines()[0] == HEADER
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["external_write"] is False
    assert data["ready"] + data["blocked"] == data["total"] == len(PRODUCTS)
    assert len(output.read_text(encoding="utf-8").splitlines()) == 1 + data["ready"]
    assert not list(tmp_path.rglob("*.parcial"))
    assert "Facebook" in result.stdout
