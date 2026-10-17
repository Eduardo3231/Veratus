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
        re.compile(r"\binspirad[oa]s?\s+(?:em|n[oa]s?)\b", re.IGNORECASE),
        "marca de terceiros",
    ),
    (
        # "movimento" já conta com a palavra seguinte: com o movimento confirmado
        # na ficha, "movimento quartzo" passa e "movimento automático" não.
        # "mecanismo" só é movimento com o tipo ("mecanismo de pagamento" passa).
        re.compile(
            r"\bmovimento\b(?:\s+(?:[eé]\s+)?\w+)?|"
            r"\bmecanismo\s+(?:[eé]\s+)?(?:autom[aá]tic|japon[eê]s|su[ií][çc]|quartz|"
            r"mec[aâ]nic)\w*|"
            r"\bm[áa]quina\s+(?:autom[áa]tica|qu[áa]rtzo?)\b|"
            r"\bquartzo\b|\bautom[áa]tico\s+de\s+corda\b",
            re.IGNORECASE,
        ),
        "especificação técnica não validada",
    ),
    (
        re.compile(
            r"\b(?:resist[eê]ncia\s+[àa]\s+[áa]gua|resistente\s+[àa]\s+[áa]gua|"
            r"\d+\s*atm|\d+\s*(?:m|metros)\s+de\s+profundidade|"
            r"pode\s+(?:mergulhar|nadar|molhar))\b",
            re.IGNORECASE,
        ),
        "resistência à água não validada",
    ),
    (
        re.compile(
            r"\b\d+(?:[,.]\d+)?\s*(?:mm|cm|mil[ií]metros|cent[ií]metros)\b|"
            r"\bdi[âa]metro\b|\bespessura\b",
            re.IGNORECASE,
        ),
        "medida não validada",
    ),
    (
        re.compile(
            r"\b(?:com|tem|possui|oferecemos|damos|inclui)\s+(?:a\s+)?garantia\b|"
            r"\bgarantia\s+(?:de\s+f[áa]brica|total|vital[ií]cia|contra)\b",
            re.IGNORECASE,
        ),
        "garantia não validada",
    ),
    (
        re.compile(
            r"\b(?:importad[oa]s?|importa[çc][ãa]o|alf[âa]ndega|taxad[oa]s?|"
            r"internacional|do\s+exterior|de\s+fora\s+do\s+pa[ií]s|china|chin[eê]s[ae]?s?|"
            r"pa[ií]s\s+de\s+origem|origem\s+d[oa]s?\s+(?:produto|pe[çc]a|rel[óo]gio)s?)\b",
            re.IGNORECASE,
        ),
        "origem ou importação não pode ser mencionada",
    ),
    (
        re.compile(
            r"\b(?:ouro|prata|banhad[oa]s?|banho\s+de|folhead[oa]s?|semijoias?|"
            r"zirc[ôo]nias?|hipoalerg[eê]nic[oa]s?|a[çc]o\s+cir[úu]rgico|"
            r"n[ãa]o\s+(?:escurece|enferruja|descasca))\b",
            re.IGNORECASE,
        ),
        "material não validado",
    ),
    (
        re.compile(
            r"\b(?:s[óo]\s+hoje|acaba\s+hoje|[úu]ltima\s+chance|corr[ae]|"
            r"por\s+tempo\s+limitado|promo[çc][ãa]o\s+rel[âa]mpago|"
            r"enquanto\s+durarem|est[aá]\s+acabando|quase\s+esgotad[oa])\b",
            re.IGNORECASE,
        ),
        "urgência ou escassez não validada",
    ),
    (
        re.compile(
            r"\b(?:dou|damos|fa[çc]o|fazemos|consigo|posso\s+(?:dar|fazer))\s+"
            r"(?:um\s+|uma\s+)?(?:desconto|abatimento|promo[çc][ãa]o)\b|\bcupom\b|"
            r"\b\d+\s*%\s*(?:off|de\s+desconto)",
            re.IGNORECASE,
        ),
        "desconto não autorizado",
    ),
    (
        re.compile(
            r"\b(?:fornecedor(?:es|a)?|f[áa]brica\s+parceira|aliexpress|shein|"
            r"alibaba|1688|dropshipping)\b",
            re.IGNORECASE,
        ),
        "menção a fornecedor",
    ),
    (
        re.compile(
            r"\b(?:OPENAI_API_KEY|VERATUS_ADMIN_TOKEN|VERATUS_AGENT_SHARED_SECRET|system prompt|prompt interno)\b",
            re.IGNORECASE,
        ),
        "conteúdo interno na resposta",
    ),
]


# Marketplaces proíbem contato externo: nenhum link, @, telefone, e-mail ou
# convite para outro canal nas respostas (regra extra do Mercado Livre).
MARKETPLACE_CHANNELS = frozenset(
    {"mercado_livre", "mercado-livre", "shopee", "tiktok_shop", "tiktok-shop", "amazon"}
)
_MARKETPLACE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"https?://|\bwww\.|\b[\w-]+\.(?:com|br|net|link|ly|me)\b|@\w+|"
            r"[^\s@]+@[^\s@]+\.[^\s@]+|\(?\d{2}\)?\s*9?\d{4}[\s.-]?\d{4}|"
            r"\b(?:whats(?:app)?|zap|wpp|instagram|insta|direct|telegram|"
            r"telefone|celular|e-?mail|site)\b",
            re.IGNORECASE,
        ),
        "contato externo em marketplace",
    ),
    (
        # Prazo em dias, preço do site, Pix e link do Mercado Pago são da venda
        # direta: no marketplace valem o anúncio e o envio dele. Frete grátis
        # continua permitido (fato confirmado para todos os canais).
        re.compile(
            r"\bem\s+at[eé]\s+\d+\s+dias?\b|\bR\$\s*\d|\bpix\b|"
            r"\bmercado\s+pago\b|\blink\s+de\s+pagamento\b",
            re.IGNORECASE,
        ),
        "condição da venda direta em marketplace",
    ),
]


# Bloqueios que um fato CONFIRMADO (ficha dos relógios ou fatos.yaml com
# ``gate_issue``) libera, só na frase desse fato: "41 mm" passa se a ficha diz
# 41 mm; "40 mm" continua bloqueado, e "tem garantia vitalícia" também, porque
# "vitalícia" não está no fato. Marca de terceiros, origem, fornecedor, urgência,
# desconto, estoque e contato externo nunca são liberados assim.
EXEMPTABLE_ISSUES = frozenset(
    {
        "especificação técnica não validada",
        "resistência à água não validada",
        "medida não validada",
        "garantia não validada",
        "material não validado",
    }
)
_STOPWORDS = frozenset(
    {
        "a",
        "o",
        "as",
        "os",
        "de",
        "da",
        "do",
        "das",
        "dos",
        "e",
        "em",
        "com",
        "tem",
        "possui",
        "oferecemos",
        "damos",
        "inclui",
        "dele",
        "dela",
        "deste",
        "desse",
        "relogio",
        "relogios",
        "modelo",
        "peca",
    }
)


def _tokens(text: str) -> set[str]:
    from .meta_webhooks import normalize_text

    normalized = normalize_text(text).replace(".", ",")
    return set(re.findall(r"\d+(?:,\d+)?|[a-z]+", normalized))


# Fim de frase: ! ? ; ou quebra de linha, ou ponto seguido de espaço ("12.5" não quebra).
_SENTENCE_BREAK = re.compile(r"[!?;\n]|\.(?=\s|$)")


def _sentence_around(text: str, position: int) -> str:
    """A frase que contém ``position``; a exceção de fato vale para a frase inteira."""
    starts = [m.end() for m in _SENTENCE_BREAK.finditer(text, 0, position)]
    ends = [m.start() for m in _SENTENCE_BREAK.finditer(text, position)]
    return text[starts[-1] if starts else 0 : ends[0] if ends else len(text)]


def _fact_allows(sentence: str, issue: str, collection: str | None) -> bool:
    if issue not in EXEMPTABLE_ISSUES:
        return False
    wanted = _tokens(sentence) - _STOPWORDS
    for fact in CONFIRMED_SALES_FACTS.values():
        if fact.get("gate_issue") != issue:
            continue
        scope = fact.get("scope")
        if scope not in (None, "all") and collection not in (None, scope):
            continue
        if wanted <= _tokens(fact["text"]):
            return True
    return False


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
    channel: str | None = None,
) -> list[str]:
    """Return the issues that stop a draft; an empty list means it may go to a human.

    ``product_collection`` is the collection of the product the draft talks
    about: the watch price never applies to jewelry.
    """
    issues: list[str] = []
    patterns = list(_BLOCKED_PATTERNS)
    if channel in MARKETPLACE_CHANNELS:
        patterns += _MARKETPLACE_PATTERNS
    for pattern, issue in patterns:
        for match in pattern.finditer(text or ""):
            if not _confirmed(match, issue, product_collection) and not _fact_allows(
                _sentence_around(text or "", match.start()), issue, product_collection
            ):
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
