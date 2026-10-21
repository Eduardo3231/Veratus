"""Repertório em knowledge/ (Fase 4): fatos, ofertas, lint e eval."""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pytest

from veratus_agents import commercial_config, knowledge
from veratus_agents.policy import hard_review_customer_reply

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def repo_knowledge() -> knowledge.Knowledge:
    return knowledge.load_knowledge(knowledge.KNOWLEDGE_DIR)


def test_facts_file_replaces_the_builtin_facts_with_the_same_refs(repo_knowledge):
    confirmed = repo_knowledge.confirmed_facts()

    for ref, fact in commercial_config._BUILTIN_SALES_FACTS.items():
        assert confirmed[ref] == fact
    assert commercial_config.CONFIRMED_SALES_FACTS == confirmed
    for ref in (
        "relogios-modelos",
        "relogios-sem-marca-de-terceiros",
        "formas-pagamento",
    ):
        assert ref in confirmed


def test_only_confirmed_facts_reach_the_agent(repo_knowledge):
    context = repo_knowledge.context()
    refs = {fact["evidence_ref"] for fact in context["fatos"]}

    assert "garantia" not in refs and "relogios-material" not in refs
    assert "Garantia." in context["a_equipe_confirma"]
    # Fato pendente não passa no gate como evidência.
    claim = {"claim_type": "warranty", "value": "1 ano", "evidence_ref": "garantia"}
    assert hard_review_customer_reply("A equipe confirma.", [claim])


def test_only_active_offers_inside_their_dates(tmp_path):
    shutil.copytree(knowledge.KNOWLEDGE_DIR, tmp_path / "k")
    (tmp_path / "k" / "ofertas.yaml").write_text(
        "schema: veratus.offers/v1\n"
        "offers:\n"
        "  - {id: a, status: ACTIVE, valid_from: 2026-10-01, valid_until: 2026-10-31,"
        " kind: kit, items: [ocean-blue, arctic-white], discount_brl: '0', text: Kit}\n"
        "  - {id: b, status: DRAFT, valid_from: 2026-10-01, valid_until: 2026-10-31,"
        " kind: kit, items: [x], discount_brl: '0', text: Rascunho}\n",
        encoding="utf-8",
    )
    loaded = knowledge.load_knowledge(tmp_path / "k")

    assert [o.id for o in loaded.active_offers(date(2026, 10, 15))] == ["a"]
    assert loaded.active_offers(date(2026, 11, 1)) == []


def test_lint_rejects_pressure_claims_and_supplier_but_not_rules(tmp_path):
    shutil.copytree(knowledge.KNOWLEDGE_DIR, tmp_path / "k")
    (tmp_path / "k" / "skills" / "fechamento.md").write_text(
        "# Fechamento\n"
        "- Nunca diga que é banhado a ouro.\n"
        "- Diga: corre que é só hoje!\n"
        "- Diga: o colar é folheado a ouro.\n"
        "- Diga: nosso fornecedor manda rápido.\n"
        "- Diga: tem garantia de fábrica.\n",
        encoding="utf-8",
    )
    problems = knowledge.lint(tmp_path / "k")
    lines = {
        item["line"] for item in problems if item["file"] == "skills/fechamento.md"
    }

    assert lines == {3, 4, 5, 6}


def test_repository_knowledge_passes_lint():
    assert knowledge.lint() == []


def test_invalid_facts_file_is_rejected(tmp_path):
    shutil.copytree(knowledge.KNOWLEDGE_DIR, tmp_path / "k")
    (tmp_path / "k" / "fatos.yaml").write_text(
        "schema: veratus.facts/v1\nfacts:\n  - {id: x, status: TALVEZ}\n",
        encoding="utf-8",
    )
    with pytest.raises(knowledge.KnowledgeError):
        knowledge.load_knowledge(tmp_path / "k")


def test_eval_runs_every_case_with_the_simulated_model():
    from veratus_agents.conversations import run_eval

    result = run_eval(knowledge.KNOWLEDGE_DIR)

    assert result["model"] == "template-v1"
    assert result["required_failures"] == 0
    assert result["total"] >= 10


def test_cli_exit_codes(capsys):
    assert knowledge.main(["lint"]) == 0
    assert knowledge.main(["eval"]) == 0
    assert '"accuracy"' in capsys.readouterr().out
