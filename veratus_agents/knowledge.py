"""Repertório de vendas em ``knowledge/``: voz, roteiro, objeções, fatos, ofertas e skills.

- ``fatos.yaml``: só ``CONFIRMED`` vira fato; o resto vira "a equipe confirma" e
  entra na fila do fundador.
- ``ofertas.yaml``: só ``ACTIVE`` dentro da validade chega ao agente.
- ``python -m veratus_agents.knowledge lint`` reprova skill ou roteiro com
  pressão, escassez, claim de material ou garantia, marca de terceiros, origem
  ou menção a fornecedor (padrões do gate em ``policy.py``). Linhas que
  enunciam uma proibição ("nunca", "não diga", "proibido", "evite") são regras,
  não falas, e não são reprovadas.
- ``python -m veratus_agents.knowledge eval`` roda ``conversas-teste.yaml`` com
  o modelo simulado (``--live`` usa o modelo real e tem custo).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[1]
KNOWLEDGE_DIR = BASE_DIR / "knowledge"
DOCUMENTS = ("voz.md", "roteiro.md", "objecoes.md")
FACT_STATUSES = ("CONFIRMED", "PENDING", "REJECTED")
OFFER_STATUSES = ("DRAFT", "ACTIVE", "PAUSED", "EXPIRED")
CLAIM_TYPES = (
    "price",
    "availability",
    "shipping",
    "delivery",
    "warranty",
    "authenticity",
    "payment",
    "specification",
    "scarcity",
    "returns",
)
# Rótulos do gate que nunca podem aparecer como fala no repertório.
LINT_ISSUES = frozenset(
    {
        "escassez não validada",
        "urgência ou escassez não validada",
        "material não validado",
        "garantia não validada",
        "origem ou importação não pode ser mencionada",
        "marca de terceiros",
        "menção a fornecedor",
        "desconto não autorizado",
    }
)
_RULE_LINE = re.compile(
    r"\b(?:nunca|jamais|n[ãa]o\s+(?:diga|use|cite|mencione|fale|prometa|ofere[çc]a)|"
    r"proibid[oa]s?|evite)\b|❌",
    re.IGNORECASE,
)


class KnowledgeError(ValueError):
    """Arquivo de repertório ausente ou fora do formato."""


@dataclass(frozen=True)
class Fact:
    id: str
    status: str
    claim_type: str
    scope: str
    text: str
    evidence: str


@dataclass(frozen=True)
class Offer:
    id: str
    status: str
    valid_from: date
    valid_until: date
    kind: str
    items: tuple[str, ...]
    discount_brl: Decimal
    text: str

    def active_on(self, today: date) -> bool:
        return self.status == "ACTIVE" and self.valid_from <= today <= self.valid_until


@dataclass(frozen=True)
class Knowledge:
    voice: str
    script: str
    objections: str
    facts: tuple[Fact, ...]
    offers: tuple[Offer, ...]
    skills: dict[str, str] = field(default_factory=dict)

    def confirmed_facts(self) -> dict[str, dict[str, str]]:
        """Formato de ``CONFIRMED_SALES_FACTS``: evidence_ref → claim."""
        return {
            fact.id: {
                "claim_type": fact.claim_type,
                "scope": fact.scope,
                "text": fact.text,
            }
            for fact in self.facts
            if fact.status == "CONFIRMED"
        }

    def pending_facts(self) -> list[Fact]:
        return [fact for fact in self.facts if fact.status != "CONFIRMED"]

    def active_offers(self, today: date | None = None) -> list[Offer]:
        today = today or datetime.now(timezone(timedelta(hours=-3))).date()
        return [offer for offer in self.offers if offer.active_on(today)]

    def context(self, *, today: date | None = None) -> dict[str, Any]:
        """O que o Sales Agent recebe: nunca custo, fornecedor ou fato pendente."""
        return {
            "voz": self.voice,
            "roteiro": self.script,
            "objecoes": self.objections,
            "fatos": [
                {"evidence_ref": ref, **fact}
                for ref, fact in self.confirmed_facts().items()
            ],
            "a_equipe_confirma": [fact.text for fact in self.pending_facts()],
            "ofertas": [
                {
                    "id": offer.id,
                    "texto": offer.text,
                    "itens": list(offer.items),
                    "desconto_brl": f"{offer.discount_brl:.2f}",
                }
                for offer in self.active_offers(today)
            ],
            "skills": dict(self.skills),
        }


def _yaml(path: Path) -> Any:
    import yaml

    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise KnowledgeError(f"{path.name}: ilegível") from exc


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise KnowledgeError(f"{path.name}: ausente") from exc


def _facts(path: Path) -> tuple[Fact, ...]:
    data = _yaml(path)
    if not isinstance(data, dict) or data.get("schema") != "veratus.facts/v1":
        raise KnowledgeError("fatos.yaml: schema deve ser veratus.facts/v1")
    facts: list[Fact] = []
    seen: set[str] = set()
    for index, item in enumerate(data.get("facts") or []):
        where = f"fatos.yaml[{index}]"
        if not isinstance(item, dict):
            raise KnowledgeError(f"{where}: fato precisa ser objeto")
        missing = [
            key
            for key in ("id", "status", "claim_type", "scope", "text", "evidence")
            if not str(item.get(key) or "").strip()
        ]
        if missing:
            raise KnowledgeError(f"{where}: faltam {missing}")
        if item["status"] not in FACT_STATUSES:
            raise KnowledgeError(f"{where}: status deve ser um de {FACT_STATUSES}")
        if item["claim_type"] not in CLAIM_TYPES:
            raise KnowledgeError(f"{where}: claim_type inválido")
        if item["id"] in seen:
            raise KnowledgeError(f"{where}: id repetido {item['id']}")
        seen.add(item["id"])
        facts.append(
            Fact(
                id=str(item["id"]).strip(),
                status=item["status"],
                claim_type=item["claim_type"],
                scope=str(item["scope"]).strip(),
                text=" ".join(str(item["text"]).split()),
                evidence=str(item["evidence"]).strip(),
            )
        )
    return tuple(facts)


def _date(value: Any, where: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise KnowledgeError(f"{where}: data inválida") from exc


def _offers(path: Path) -> tuple[Offer, ...]:
    if not path.exists():
        return ()
    data = _yaml(path)
    if not isinstance(data, dict) or data.get("schema") != "veratus.offers/v1":
        raise KnowledgeError("ofertas.yaml: schema deve ser veratus.offers/v1")
    offers = []
    for index, item in enumerate(data.get("offers") or []):
        where = f"ofertas.yaml[{index}]"
        if not isinstance(item, dict) or item.get("status") not in OFFER_STATUSES:
            raise KnowledgeError(f"{where}: status deve ser um de {OFFER_STATUSES}")
        try:
            discount = Decimal(str(item.get("discount_brl", "0")))
        except InvalidOperation as exc:
            raise KnowledgeError(f"{where}: discount_brl inválido") from exc
        offer = Offer(
            id=str(item.get("id") or "").strip(),
            status=item["status"],
            valid_from=_date(item.get("valid_from"), f"{where}.valid_from"),
            valid_until=_date(item.get("valid_until"), f"{where}.valid_until"),
            kind=str(item.get("kind") or "single"),
            items=tuple(str(sku) for sku in item.get("items") or ()),
            discount_brl=discount,
            text=" ".join(str(item.get("text") or "").split()),
        )
        if not offer.id or not offer.text or offer.valid_until < offer.valid_from:
            raise KnowledgeError(f"{where}: id, texto e validade são obrigatórios")
        offers.append(offer)
    return tuple(offers)


def load_knowledge(directory: str | Path = KNOWLEDGE_DIR) -> Knowledge:
    directory = Path(directory)
    skills = {
        path.stem: _text(path) for path in sorted((directory / "skills").glob("*.md"))
    }
    return Knowledge(
        voice=_text(directory / "voz.md"),
        script=_text(directory / "roteiro.md"),
        objections=_text(directory / "objecoes.md"),
        facts=_facts(directory / "fatos.yaml"),
        offers=_offers(directory / "ofertas.yaml"),
        skills=skills,
    )


@lru_cache(maxsize=2)
def _cached(directory: str) -> Knowledge:
    return load_knowledge(directory)


def default_knowledge() -> Knowledge:
    return _cached(str(KNOWLEDGE_DIR))


def confirmed_sales_facts(
    fallback: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    """Fatos confirmados de ``fatos.yaml``; sem o arquivo, os fatos embutidos."""
    try:
        facts = default_knowledge().confirmed_facts()
    except (KnowledgeError, ImportError):
        return dict(fallback)
    return facts or dict(fallback)


# -- lint -----------------------------------------------------------------------


def lint_text(name: str, text: str) -> list[dict[str, Any]]:
    from .policy import hard_review_customer_reply

    problems = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or _RULE_LINE.search(line):
            continue
        issues = sorted(
            set(hard_review_customer_reply(line, product_collection=None)) & LINT_ISSUES
        )
        if issues:
            problems.append({"file": name, "line": number, "issues": issues})
    return problems


def lint(directory: str | Path = KNOWLEDGE_DIR) -> list[dict[str, Any]]:
    directory = Path(directory)
    knowledge = load_knowledge(directory)
    problems = []
    for name, text in (
        ("roteiro.md", knowledge.script),
        ("objecoes.md", knowledge.objections),
        ("voz.md", knowledge.voice),
        *((f"skills/{key}.md", value) for key, value in knowledge.skills.items()),
        *((f"ofertas.yaml:{offer.id}", offer.text) for offer in knowledge.offers),
        *(
            (f"fatos.yaml:{fact.id}", fact.text)
            for fact in knowledge.facts
            if fact.status == "CONFIRMED"
        ),
    ):
        problems.extend(lint_text(name, text))
    for offer in knowledge.offers:
        if offer.discount_brl > 0:
            from .conversations import discount_limit

            if offer.discount_brl > discount_limit():
                problems.append(
                    {
                        "file": f"ofertas.yaml:{offer.id}",
                        "line": 0,
                        "issues": ["desconto acima de DISCOUNT_LIMIT_BRL"],
                    }
                )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m veratus_agents.knowledge")
    sub = parser.add_subparsers(dest="command", required=True)
    lint_cmd = sub.add_parser("lint", help="reprova pressão, claims e fornecedor")
    lint_cmd.add_argument("--dir", default=str(KNOWLEDGE_DIR))
    eval_cmd = sub.add_parser("eval", help="roda conversas-teste.yaml")
    eval_cmd.add_argument("--dir", default=str(KNOWLEDGE_DIR))
    eval_cmd.add_argument(
        "--live", action="store_true", help="usa o modelo real (tem custo)"
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "lint":
            problems = lint(args.dir)
            print(json.dumps({"problems": problems}, ensure_ascii=False, indent=2))
            return 1 if problems else 0
        from .conversations import run_eval

        report = run_eval(Path(args.dir), live=args.live)
    except KnowledgeError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["required_failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
