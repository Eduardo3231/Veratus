from __future__ import annotations

import re

# O catálogo público atual não valida preços, prazos, garantia, origem ou frete.
# Este gate é propositalmente pequeno e determinístico: ele bloqueia afirmações
# comerciais de alto risco mesmo se os dois agentes de IA deixarem passar.
_BLOCKED_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"R\$\s*\d", re.IGNORECASE), "valor monetário não validado"),
    (
        re.compile(r"\b\d[\d.]*[,.]?\d*\s*(?:reais|brl)\b", re.IGNORECASE),
        "valor monetário não validado",
    ),
    (
        re.compile(r"\bfrete\s+(gr[aá]tis|incluso)\b", re.IGNORECASE),
        "condição de frete não validada",
    ),
    (
        re.compile(r"\bfrete\s+(?:por\s+nossa\s+conta|sem\s+custo)\b", re.IGNORECASE),
        "condição de frete não validada",
    ),
    (
        re.compile(r"\b(?:envio|entrega)\s+em\s+at[eé]\s+\d+", re.IGNORECASE),
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
            r"\b(?:OPENAI_API_KEY|VERATUS_ADMIN_TOKEN|VERATUS_AGENT_SHARED_SECRET|system prompt|prompt interno)\b",
            re.IGNORECASE,
        ),
        "conteúdo interno na resposta",
    ),
]


def hard_review_customer_reply(
    text: str, claims: list[dict[str, object]] | None = None
) -> list[str]:
    issues: list[str] = []
    for pattern, issue in _BLOCKED_PATTERNS:
        if pattern.search(text or ""):
            issues.append(issue)
    for claim in claims or []:
        if not str(claim.get("evidence_ref") or "").strip():
            issues.append(
                f"claim {claim.get('claim_type', 'comercial')} sem evidência validada"
            )
    return list(dict.fromkeys(issues))
