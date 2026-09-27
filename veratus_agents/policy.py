from __future__ import annotations

import re

from .commercial_config import (
    CONFIRMED_SALES_FACTS,
    DELIVERY_MAX_DAYS,
    OFFICIAL_SALE_PRICE_BRL,
)

# Gate determinístico: bloqueia afirmações comerciais de alto risco mesmo se os
# dois agentes de IA deixarem passar. Os fatos confirmados pelo fundador
# (CONFIRMED_SALES_FACTS) passam; qualquer variação deles continua bloqueada.
_PRICE_TEXT = f"{OFFICIAL_SALE_PRICE_BRL:.2f}".replace(".", ",")

_BLOCKED_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"R\$\s*(\d[\d.]*(?:,\d{1,2})?)", re.IGNORECASE),
        "valor monetário não validado",
    ),
    (
        re.compile(r"\b(\d[\d.]*(?:,\d{1,2})?)\s*(?:reais|brl)\b", re.IGNORECASE),
        "valor monetário não validado",
    ),
    (
        re.compile(r"\b(?:envio|entrega)\s+em\s+at[eé]\s+(\d+)", re.IGNORECASE),
        "prazo não validado",
    ),
    (
        re.compile(
            r"\b(?:chega|entrego|entregamos)\s+(?:amanh[ãa]|hoje|em\s+\d+\s+dias?)\b",
            re.IGNORECASE,
        ),
        "prazo não validado",
    ),
    (re.compile(r"\bgarantia\s+(?:de\s+)?\d+", re.IGNORECASE), "garantia não validada"),
    (
        re.compile(
            r"\bgarantia\s+(?:por\s+)?(?:um|uma|dois|duas)\s+(?:ano|anos|m[eê]s|meses)\b",
            re.IGNORECASE,
        ),
        "garantia não validada",
    ),
    (
        re.compile(
            r"\b(?:temos?|est[aá])\s+(?:dispon[ií]vel|em\s+estoque|[àa]\s+pronta\s+entrega)\b",
            re.IGNORECASE,
        ),
        "estoque/disponibilidade não validado",
    ),
    (
        re.compile(
            r"\b(?:restam?|[uú]ltimas?)\s+(?:\d+\s+)?(?:unidades?|pe[çc]as?)\b",
            re.IGNORECASE,
        ),
        "escassez não validada",
    ),
    (
        re.compile(
            r"\b(?:aceitamos?|parcelamos?|em\s+at[eé])\s+(?:em\s+)?\d{1,2}x\b",
            re.IGNORECASE,
        ),
        "condição de pagamento não validada",
    ),
    (
        re.compile(
            r"\b(?:reservo|reservamos|separo|separamos|guardo|guardamos)\s+(?:para\s+)?(?:voc[eê]|você)\b",
            re.IGNORECASE,
        ),
        "reserva não validada",
    ),
    (
        re.compile(
            r"\b(?:movimento|mecanismo)\s+(?:autom[aá]tico|japon[eê]s|su[ií][çc]o|quartz)\b",
            re.IGNORECASE,
        ),
        "especificação técnica não validada",
    ),
    (
        re.compile(
            r"\b(?:100%\s+)?(?:original|aut[eê]ntico|autenticidade garantida)\b",
            re.IGNORECASE,
        ),
        "origem/autenticidade não validada",
    ),
    (
        re.compile(r"\b(?:[àa]\s+prova\s+d['’]?água|waterproof)\b", re.IGNORECASE),
        "resistência à água não validada",
    ),
    (
        re.compile(
            r"\b(?:rolex|submariner|datejust|gmt[\s-]*master|daytona|yacht[\s-]*master|"
            r"omega|cartier|patek|audemars|hublot|tag\s+heuer|tissot|seiko)\b",
            re.IGNORECASE,
        ),
        "marca de terceiros",
    ),
    (
        re.compile(
            r"\b(?:r[ée]plicas?|primeira\s+linha|aaa\+?|clone|inspirad[oa]\s+n[oa])\b",
            re.IGNORECASE,
        ),
        "marca de terceiros",
    ),
    (
        re.compile(
            r"\b(?:OPENAI_API_KEY|VERATUS_ADMIN_TOKEN|VERATUS_AGENT_SHARED_SECRET|system prompt|prompt interno)\b",
            re.IGNORECASE,
        ),
        "conteúdo interno na resposta",
    ),
]


def _confirmed(match: re.Match[str], issue: str, collection: str | None) -> bool:
    """A match that states exactly a founder-confirmed fact is allowed."""
    if issue == "valor monetário não validado":
        amount = match.group(1).replace(".", ",")
        return amount == _PRICE_TEXT and collection in (None, "watches")
    if issue == "prazo não validado" and match.lastindex:
        # Only the delivery term was confirmed, not a shipping (dispatch) term.
        return match.group(0).lower().startswith("entrega") and (
            int(match.group(1)) == DELIVERY_MAX_DAYS
        )
    return False


def hard_review_customer_reply(
    text: str,
    claims: list[dict[str, object]] | None = None,
    *,
    product_collection: str | None = None,
) -> list[str]:
    """Return the issues that stop a draft; an empty list means it may go to a human.

    ``product_collection`` is the collection of the product the draft talks
    about: the watch price never applies to jewelry.
    """
    issues: list[str] = []
    for pattern, issue in _BLOCKED_PATTERNS:
        for match in pattern.finditer(text or ""):
            if not _confirmed(match, issue, product_collection):
                issues.append(issue)
                break
    for claim in claims or []:
        claim_type = claim.get("claim_type", "comercial")
        fact = CONFIRMED_SALES_FACTS.get(str(claim.get("evidence_ref") or "").strip())
        if fact is None or fact["claim_type"] != claim_type:
            issues.append(f"claim {claim_type} sem evidência validada")
        elif fact["scope"] == "watches" and product_collection not in (None, "watches"):
            issues.append(f"claim {claim_type} não vale para esta coleção")
    return list(dict.fromkeys(issues))
