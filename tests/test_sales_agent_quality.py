"""The sales agent may say what the founder confirmed, and nothing beyond it."""

from __future__ import annotations

import json
import re

from veratus_agents.catalog import load_catalog
from veratus_agents.commercial_config import CONFIRMED_SALES_FACTS
from veratus_agents.policy import hard_review_customer_reply
from veratus_agents.workflow import (
    SALES_INSTRUCTIONS,
    _make_tools,
    commercial_policy,
    sales_view,
)

PRIVATE_FIELDS = {
    "cost",
    "sale_price",
    "supplier_visibility",
    "channel_stock_cap",
    "material",
    "inventory_mode",
    "financial_status",
}


def _tools() -> dict:
    return {tool.__name__: tool for tool in _make_tools(lambda function: function)}


def test_confirmed_terms_pass_the_gate_with_their_evidence() -> None:
    reply = (
        "O Ocean Blue sai por R$ 289,90, com frete grátis e entrega em até 7 dias, "
        "sem taxas adicionais. Você pode desistir em até 7 dias após o recebimento."
    )
    claims = [
        {"claim_type": "price", "value": "R$ 289,90", "evidence_ref": "preco-relogios"},
        {
            "claim_type": "shipping",
            "value": "frete grátis",
            "evidence_ref": "frete-gratis",
        },
        {
            "claim_type": "delivery",
            "value": "até 7 dias",
            "evidence_ref": "entrega-7-dias",
        },
        {"claim_type": "price", "value": "sem taxas", "evidence_ref": "sem-taxas"},
        {
            "claim_type": "returns",
            "value": "7 dias",
            "evidence_ref": "desistencia-7-dias",
        },
    ]

    assert hard_review_customer_reply(reply, claims, product_collection="watches") == []


def test_every_other_commercial_claim_is_still_blocked() -> None:
    blocked = [
        "O Ocean Blue sai por R$ 199,90.",
        "Fica 250 reais.",
        "Entrega em até 3 dias.",
        "Envio em até 7 dias.",
        "Chega amanhã.",
        "É igual ao Rolex Submariner.",
        "Réplica primeira linha.",
        "Temos em estoque.",
        "Parcelamos em 10x.",
    ]
    for text in blocked:
        assert hard_review_customer_reply(text, product_collection="watches"), text


def test_watch_price_never_applies_to_jewelry() -> None:
    claim = {
        "claim_type": "price",
        "value": "R$ 289,90",
        "evidence_ref": "preco-relogios",
    }

    assert hard_review_customer_reply(
        "O Halo Verde sai por R$ 289,90.", product_collection="feminine"
    )
    assert hard_review_customer_reply("", [claim], product_collection="feminine")


def test_claims_need_a_known_evidence_of_the_same_type() -> None:
    unknown = {"claim_type": "price", "value": "R$ 289,90", "evidence_ref": "site"}
    mismatched = {
        "claim_type": "warranty",
        "value": "1 ano",
        "evidence_ref": "frete-gratis",
    }

    assert hard_review_customer_reply("", [unknown])
    assert hard_review_customer_reply("", [mismatched])


def test_prompt_examples_pass_the_gate() -> None:
    examples = re.findall(r"→ '([^']+)'", SALES_INSTRUCTIONS)

    assert len(examples) == 2
    for reply in examples:
        assert hard_review_customer_reply(reply, product_collection="watches") == []


def test_agent_tools_expose_the_price_and_never_private_fields() -> None:
    tools = _tools()
    catalog = json.loads(tools["listar_catalogo"]())
    by_id = {item["id"]: item for item in catalog}
    ocean = json.loads(tools["consultar_produto"]("Ocean Blue"))["product"]

    assert len(catalog) == 18 and "black-gmt" in by_id
    assert ocean["price_brl"] == "289,90"
    assert ocean["price_evidence_ref"] == "preco-relogios"
    assert by_id["halo-verde"]["price_brl"] is None
    assert "tom dourado" in by_id["halo-verde"]["copy_rule"]
    assert json.loads(tools["consultar_produto"]("Relógio inexistente")) == {
        "found": False
    }
    for product in load_catalog():
        assert not PRIVATE_FIELDS & set(sales_view(product))


def test_commercial_policy_lists_every_confirmed_fact() -> None:
    policy = commercial_policy()
    refs = {fact["evidence_ref"] for fact in policy["confirmed_facts"]}

    assert refs == set(CONFIRMED_SALES_FACTS)
    assert "formas e condições de pagamento" in policy["must_confirm_with_team"]
    assert any("marcas de terceiros" in rule for rule in policy["never"])
    assert any("fornecedor" in rule for rule in policy["never"])
