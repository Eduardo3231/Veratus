"""Texto de anúncio de marketplace a partir do catálogo, pronto para revisão.

``listing_copy(produto, canal)`` monta, para Mercado Livre, Shopee, Amazon ou
TikTok Shop: título, descrição (parágrafos curtos), destaques, atributos
públicos, fotos, preço público e os bloqueios que ainda impedem o anúncio.
Nada é publicado: é rascunho para o fundador revisar.

Regras:

- só campos públicos (os de ``catalog.public_catalog``) e fatos CONFIRMED que
  valem no marketplace (especificação e garantia). Preço, frete, prazo,
  pagamento e devolução são do marketplace e ficam fora do texto;
- relógio: material, movimento, resistência à água e medidas só com fato da
  ficha (``catalog/ficha-relogios.yaml``), na frase exata do fato;
- joia: só "tom dourado" ou "tom prateado";
- todo texto passa por ``policy.hard_review_customer_reply`` com o canal; frase
  que não passa (no gate ou nas regras daqui) fica de fora.

``python scripts/exportar_anuncios.py --saida <arquivo.json>`` gera todos.
"""

from __future__ import annotations

import colorsys
import re
from typing import Any

from . import commercial_config
from .catalog import load_catalog, public_catalog, public_price
from .meta_webhooks import normalize_text
from .policy import hard_review_customer_reply

CHANNELS = ("mercado_livre", "shopee", "amazon", "tiktok_shop")
CHANNEL_LABELS = {
    "mercado_livre": "Mercado Livre",
    "shopee": "Shopee",
    "amazon": "Amazon",
    "tiktok_shop": "TikTok Shop",
}
_CHANNEL_ALIASES = {"mercado-livre": "mercado_livre", "tiktok-shop": "tiktok_shop"}

# Limite de caracteres do título. SUPOSIÇÃO: valores usuais da documentação de
# vendedor de cada canal, não conferidos na conta (mudam por categoria).
# Conferir no painel antes de publicar.
TITLE_LIMITS = {"mercado_livre": 60, "shopee": 120, "amazon": 200, "tiktok_shop": 255}
TITLE_LIMITS_STATUS = "ASSUMPTION"

# Igual a ON_HOLD de scripts/build_landing.py (o teste confere): fora do site
# enquanto o alerta de semelhança com marca de terceiros estiver aberto.
ON_HOLD = frozenset({"noir-clover"})

MAX_HIGHLIGHTS = 5

# Fatos que valem no texto do marketplace. Preço, frete, prazo, pagamento e
# devolução são do marketplace; autenticidade e origem nunca entram.
_FACT_TYPES = frozenset({"specification", "warranty"})
# A lista dos nove modelos não cabe no anúncio de um.
_FACT_SKIP = frozenset({"relogios-modelos"})
_FACT_ORDER = (
    "relogios-movimento",
    "relogios-material-caixa",
    "relogios-material-pulseira",
    "relogios-resistencia-agua",
    "relogios-diametro",
    "relogios-espessura",
    "relogios-largura-pulseira",
    "relogios-garantia",
)
# Campos confirmados da ficha que viram atributo do anúncio (envio, prazo,
# embalagem e estoque ficam com o marketplace e o Product Master).
_SHEET_ATTRIBUTES = (
    "material_caixa",
    "material_pulseira",
    "movimento",
    "resistencia_agua",
    "diametro_caixa_mm",
    "espessura_caixa_mm",
    "largura_pulseira_mm",
    "garantia",
    "cor_mostrador",
    "cor_pulseira",
    "gtin",
)

# Cor do acabamento do relógio pela paleta pública (cor, não material).
_METAL_TONE = {
    "steel": "tom prateado",
    "gold": "tom dourado",
    "two-tone": "tom prateado e dourado",
}
# O mesmo de JEWELRY_TONE em scripts/build_landing.py.
_JEWEL_TONE = {"silver_tone": "tom prateado", "gold_tone": "tom dourado"}
# subcategoria → (tipo, gênero, frase de uso, destaque de uso)
_JEWEL_KIND = {
    "necklace": (
        "Colar",
        "feminino",
        "Usado sozinho, valoriza o decote; em camadas, acompanha outras correntes.",
        "Para usar sozinho ou em camadas",
    ),
    "bracelet": (
        "Pulseira",
        "feminina",
        "Usada sozinha, é discreta; combinada, acompanha outras pulseiras e o relógio.",
        "Para usar sozinha ou com outras pulseiras",
    ),
    "anklet": (
        "Tornozeleira",
        "feminina",
        "Leve no tornozelo, acompanha sandálias, rasteirinhas e looks de verão.",
        "Leve para o dia a dia e o verão",
    ),
}
_JEWEL_TYPE = re.compile(
    r"^(?P<head>.+?)\s+em\s+tom\s+(?P<tone>dourado|prateado)(?P<tail>.*)$",
    re.IGNORECASE,
)
_STYLE_WORDS = {
    "classico": "clássico",
    "esportivo": "esportivo",
    "marcante": "marcante",
}
# estilo → (frase da descrição, destaque)
_STYLE_LINES = {
    "classico": (
        "Linhas sóbrias que acompanham o trabalho, um jantar ou uma ocasião formal.",
        "Visual sóbrio para o trabalho e ocasiões formais",
    ),
    "esportivo": (
        (
            "Visual esportivo para o dia a dia, que também acompanha uma produção "
            "mais arrumada."
        ),
        "Visual esportivo para o dia a dia",
    ),
    "marcante": (
        "Para quem gosta de um relógio que se destaca no pulso.",
        "Presença marcante no pulso",
    ),
    "versatil": (
        "Combina tanto com jeans quanto com roupa social.",
        "Combina com jeans e com roupa social",
    ),
}
_WATCH_GIFT = ("Também é uma boa opção de presente.", "Boa opção de presente")
# Pergunta pelo próprio canal; a Amazon não aceita convite de contato no texto.
_QUESTION_LINE = {
    "mercado_livre": "Ficou com alguma dúvida? Faça sua pergunta aqui no anúncio.",
    "shopee": "Ficou com alguma dúvida? Fale com a gente pelo chat da loja.",
    "tiktok_shop": "Ficou com alguma dúvida? Fale com a gente pelo chat da loja.",
}
_SMALL_WORDS = frozenset({"a", "o", "e", "em", "com", "de", "da", "do", "das", "dos"})

# Regras do texto de anúncio além do gate (texto sem acento, minúsculo). Não
# valem para a frase exata de um fato confirmado.
_RULES_ALL = (
    (
        (
            r"\bfrete|\bentreg|\benvio\b|\benviamos|\bprazo|\bdias? uteis|\bchega\b|"
            r"\bpostag|\bretirada"
        ),
        "frete e prazo são do marketplace",
    ),
    (
        (
            r"r\$|\breais\b|\bpreco|\bparcel|\bpix\b|\bboleto|\bdesconto|\bcupo[mn]|"
            r"\bpromoc|\boferta|\bgratis"
        ),
        "preço e pagamento são do marketplace",
    ),
    (
        (
            r"https?:|www\.|@|\bwhats|\bzap\b|\bwpp\b|\binsta|\btelegram|\btelefone|"
            r"\bcelular|\be-?mail|\bsite\b|\bcontato|\bdirect\b"
        ),
        "contato ou link externo",
    ),
    (
        (
            r"\boriginal|\bautentic|\bautoriza|\bcertificad|\bimporta[dc]|\borigem|"
            r"\bnacional\b|\bfabricad|\bproduzid|\bbrasil|\bsuic|\bjapon|\bchin[ae]|"
            r"\bexterior"
        ),
        "origem ou autenticidade",
    ),
    (
        (
            r"\bultim[oa]s?\b|\bso hoje|\bacaba (?:hoje|logo)|\bestoque|\besgot|"
            r"\bcorr[ae]\b|\blimitad|\bexclusiv|\bimperd|\bpronta entrega|\bdisponi|"
            r"\bunidades?\b"
        ),
        "urgência, escassez ou estoque",
    ),
    (
        (
            r"\bmelhor(?:es)?\b|\bnumero 1\b|\bperfeit|\bincrivel|\bluxo|\bluxuos|"
            r"\bgarantid|\bgarantia|\b100 ?%"
        ),
        "promessa sem fato confirmado",
    ),
    (
        (
            r"\breplica|\bclone|\binspirad|\brolex|\bomega\b|\bcartier|\btiffany|"
            r"\bvivara|\bpandora|\bvan cleef|\balhambra|\bb[uv]lgari|\bswarovski|"
            r"\bchanel|\bdior\b|\bgucci|\bvuitton|\bmichael kors|\binvicta|\bcasio|"
            r"\btechnos|\borient\b|\bcitizen|\bfossil|\barmani|\btudor|\bbreitling|"
            r"\bheuer|\bnixon|\bpuma\b|\bu-?boat|\bhublot|\bpatek|\baudemars|"
            r"\bseiko|\btissot"
        ),
        "marca de terceiros",
    ),
)
_RULES_WATCH = (
    (
        (
            r"\bacos?\b|\binox|\bmetal|\btitanio|\bcouro|\bsafira|\bcristal|\bvidro|"
            r"\bmineral|\bceramic|\bresina|\bsilicone|\bborracha|\bmaterial|\bnylon"
        ),
        "material de relógio sem fato da ficha",
    ),
    (
        (
            r"\bmovimento|\bmaquina|\bmecanism|\bquartz|\bautomatic|\bbateria|"
            r"\bcorda\b|\bcronograf|\bcalendario|\bfunc(?:ao|oes)\b|\bdata\b|\bluminos"
        ),
        "especificação de relógio sem fato da ficha",
    ),
    (
        r"\bprova d|\bagua\b|\batm\b|\bmergulh|\bnatac|\brespingo",
        "resistência à água sem fato da ficha",
    ),
    (
        (
            r"\bmm\b|\bcm\b|\bmilimetr|\bcentimetr|\bdiametro|\bespessura|\blargura|"
            r"\btamanho|\bpeso\b|\bgramas?\b"
        ),
        "medida sem fato da ficha",
    ),
)
_RULES_JEWEL = (
    (
        (
            r"\bouro|\bprata\b|\bbanh|\bfolhead|\bsemi-?joia|\bzirc|\bhipoalerg|"
            r"\bantialerg|\bacos?\b|\binox|\bmetal|\brodio|\blatao|\bcristal|\bpedra|"
            r"\bstrass|\bperola|\bmaterial|\bescurec|\benferruj|\bdescasc|\bdesbot|"
            r"\b\d+ ?k\b|\bquilat"
        ),
        "material de joia sem fato confirmado",
    ),
    (
        (
            r"\bmm\b|\bcm\b|\bcomprimento|\btamanho|\bpeso\b|\bgramas?\b|\bajustave|"
            r"\bextensor"
        ),
        "medida de joia sem fato confirmado",
    ),
    (r"(?<!tom )\b(?:dourad|pratead)", "joia: só tom dourado ou tom prateado"),
)
_COMPILED = {
    name: tuple((re.compile(pattern), issue) for pattern, issue in rules)
    for name, rules in (
        ("all", _RULES_ALL),
        ("watches", _RULES_WATCH),
        ("feminine", _RULES_JEWEL),
    )
}

BLOCKER_TEXTS = {
    "ON_HOLD": (
        "Fora do site enquanto o alerta de semelhança com marca de terceiros "
        "estiver aberto (decisão do fundador)."
    ),
    "NOT_PUBLIC": "Produto fora do catálogo público: não vira anúncio.",
    "PRODUCT_DRAFT": (
        "Produto em rascunho (DRAFT) no catálogo: precisa ser aprovado antes de "
        "virar anúncio."
    ),
    "PRODUCT_INACTIVE": "Produto fora de venda no catálogo.",
    "NEEDS_PRICING": "Preço de venda ainda sem fonte (NEEDS_PRICING).",
    "PRICE_MISSING": "Sem preço público: o anúncio não sobe sem preço.",
    "NEEDS_REAL_PHOTO": (
        "Falta a foto real da peça, sem marca de terceiros (entra pelo "
        "importador de fotos)."
    ),
    "MEDIA_IN_QUARANTINE": (
        "A imagem usada no site está na quarentena (mostra marca de terceiros) "
        "e não vai para o anúncio."
    ),
    "NO_PHOTO": "Nenhuma foto liberada para o anúncio.",
    "ORIGIN_UNCONFIRMED": (
        "Falta confirmar na ficha que o relógio sai de dentro do Brasil "
        "(envio_do_brasil)."
    ),
    "FICHA:invalida": (
        "A ficha dos relógios está ausente ou com erro "
        "(python -m veratus_agents.watch_sheet check)."
    ),
    "GATE_ISSUES": "O texto não passou no gate de claims: veja issues.",
}


class UnknownChannelError(ValueError):
    """Canal fora de Mercado Livre, Shopee, Amazon e TikTok Shop."""


def normalize_channel(channel: str) -> str:
    """'mercado-livre' (ou o Marketplace do adaptador) → 'mercado_livre'."""
    value = str(getattr(channel, "value", channel)).strip().lower()
    value = _CHANNEL_ALIASES.get(value, value)
    if value not in CHANNELS:
        raise UnknownChannelError(f"canal sem texto de anúncio: {channel}")
    return value


def find_catalog_entry(product: dict[str, Any]) -> dict[str, Any] | None:
    """O item do catálogo com o mesmo id ou nome (sem busca aproximada)."""
    keys = {
        normalize_text(str(product.get(field) or "")).strip()
        for field in ("id", "name")
    } - {""}
    for entry in load_catalog():
        if {normalize_text(entry["id"]), normalize_text(entry["name"])} & keys:
            return entry
    return None


# -- texto ------------------------------------------------------------------------


class _Writer:
    """Confere cada frase no gate do canal e nas regras do anúncio."""

    def __init__(self, channel: str, collection: str, name: str) -> None:
        self.channel = channel
        self.collection = collection
        self.name = name

    def gate(self, text: str) -> list[str]:
        return hard_review_customer_reply(
            text, product_collection=self.collection, channel=self.channel
        )

    def rules(self, text: str) -> list[str]:
        # O nome do modelo é público ("Celeste Link", "Bronze Heritage").
        plain = normalize_text(text.replace(self.name, " "))
        rules = _COMPILED["all"] + _COMPILED.get(self.collection, ())
        return [issue for pattern, issue in rules if pattern.search(plain)]

    def ok(self, text: str | None) -> str | None:
        """A frase, se passa no gate e nas regras; senão ``None``."""
        if text and not self.rules(text) and not self.gate(text):
            return text
        return None

    def fact_ok(self, text: str | None) -> str | None:
        """Frase exata de fato confirmado: só o gate (ele libera o fato)."""
        return text if text and not self.gate(text) else None


def _sentence(text: str) -> str:
    text = " ".join(text.split())
    return text if text.endswith((".", "?", "!")) else text + "."


def _bullet(text: str) -> str:
    return " ".join(text.split()).rstrip(".")


def _join_words(words: list[str]) -> str:
    """['clássico', 'versátil'] → 'clássico e versátil'."""
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + " e " + words[-1]


def _title_case(text: str) -> str:
    words = text.split()
    return " ".join(
        word if index and word.lower() in _SMALL_WORDS else word[:1].upper() + word[1:]
        for index, word in enumerate(words)
    )


def _fit(parts: list[tuple[str, int]], limit: int) -> str:
    """Junta as partes; tira as opcionais (prioridade maior primeiro) até caber."""
    keep = [part for part in parts if part[0]]
    while len("".join(text for text, _ in keep)) > limit:
        optional = [part for part in keep if part[1] > 0]
        if not optional:
            text = "".join(t for t, _ in keep)[: limit + 1]
            return text.rsplit(" ", 1)[0].rstrip(" ,")
        keep.remove(max(optional, key=lambda part: part[1]))
    return "".join(text for text, _ in keep)


def _paragraphs(blocks: list[list[str | None]]) -> str:
    return "\n\n".join(
        " ".join(sentence for sentence in block if sentence)
        for block in blocks
        if any(block)
    )


def _dial_color(palette: dict[str, Any] | None) -> str | None:
    """Cor do mostrador pela paleta pública (hex)."""
    value = str((palette or {}).get("dial") or "").lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", value):
        return None
    red, green, blue = (int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))
    hue, light, saturation = colorsys.rgb_to_hls(red, green, blue)
    hue *= 360
    if light < 0.12:
        return "preto"
    if light >= 0.85:
        return "branco"
    if saturation < 0.2:
        return "prateado" if light >= 0.55 else "cinza"
    if hue < 15 or hue >= 330:
        return "vermelho"
    if hue < 50:
        return "marrom" if light < 0.45 else "dourado"
    if hue < 80:
        return "amarelo"
    if hue < 180:
        return "verde"
    if hue < 260:
        return "azul"
    return "roxo"


def _confirmed_facts(collection: str) -> list[str]:
    """Textos dos fatos CONFIRMED que valem no marketplace para este produto."""
    facts = commercial_config.CONFIRMED_SALES_FACTS
    refs = [
        ref
        for ref, fact in facts.items()
        if ref not in _FACT_SKIP
        and not ref.endswith("-cores")
        and fact.get("claim_type") in _FACT_TYPES
        and (fact.get("scope") or "all") in ("all", collection)
    ]
    order = {ref: index for index, ref in enumerate(_FACT_ORDER)}
    refs.sort(key=lambda ref: (order.get(ref, len(order)), ref))
    return [_sentence(str(facts[ref]["text"])) for ref in refs]


def _color_fact(model: str) -> str | None:
    fact = commercial_config.CONFIRMED_SALES_FACTS.get(f"{model}-cores")
    if fact and fact.get("scope") in ("watches", None):
        return _sentence(str(fact["text"]))
    return None


def _sheet():
    from .watch_sheet import default_sheet

    return default_sheet()


def _watch_styles(item: dict[str, Any]) -> list[str]:
    eyebrow = [
        part.strip().lower()
        for part in str(item.get("eyebrow") or "").split("/")
        if part.strip()
    ]
    if eyebrow:
        return eyebrow
    return [_STYLE_WORDS.get(style, style) for style in item.get("styles") or []]


def _watch_copy(item: dict[str, Any], writer: _Writer) -> dict[str, Any]:
    name, model, channel = item["name"], item["id"], writer.channel
    styles = _watch_styles(item)
    style_text = _join_words(styles)
    tone = _METAL_TONE.get(str((item.get("palette") or {}).get("metal")))
    sheet = _sheet()
    sheet_dial = sheet.confirmed("cor_mostrador", model) if sheet else None
    dial = next(
        (
            color.lower()
            for color in (sheet_dial, _dial_color(item.get("palette")))
            if color and writer.ok(f"Mostrador {color.lower()}.")
        ),
        None,
    )
    # Cores confirmadas na ficha valem mais que a paleta do site.
    color_fact = writer.fact_ok(_color_fact(model))
    look = color_fact
    if not look and tone and dial and tone == f"tom {dial}":
        look = writer.ok(f"Mostrador e acabamento em {tone}.")
    elif not look and (dial or tone):
        parts = [
            f"mostrador {dial}" if dial else "",
            f"acabamento em {tone}" if tone else "",
        ]
        look = writer.ok(_sentence(" e ".join(filter(None, parts)).capitalize()))
    story = writer.ok(_sentence(item.get("description") or ""))
    facts = [fact for fact in _confirmed_facts("watches") if writer.fact_ok(fact)]
    style_keys = [normalize_text(style) for style in styles]
    style_lines = [
        writer.ok(_STYLE_LINES[key][0]) for key in style_keys if key in _STYLE_LINES
    ]

    description = _paragraphs(
        [
            [
                writer.ok(f"O relógio Veratus {name} tem estilo {style_text}.")
                if style_text
                else None,
                story,
            ],
            [look, *style_lines],
            facts,
            [writer.ok(_WATCH_GIFT[0])],
            [
                writer.ok(f"Você recebe 1 relógio Veratus {name}."),
                writer.ok(_QUESTION_LINE.get(channel)),
            ],
        ]
    )

    style_title = _title_case(style_text)
    dial_title = _title_case(f"mostrador {dial}") if dial else ""
    tone_title = _title_case(f"acabamento em {tone}") if tone else ""
    if channel == "amazon":
        parts = [
            (f"Veratus Relógio {name}", 0),
            (f", {style_title}" if style_title else "", 2),
            (f", {dial_title}" if dial_title else "", 1),
            (f", {tone_title}" if tone_title else "", 3),
        ]
    elif channel == "mercado_livre":
        parts = [
            (f"Relógio Veratus {name}", 0),
            (f" {_title_case(styles[0])}" if styles else "", 2),
            (f" {dial_title}" if dial_title else "", 1),
        ]
    else:
        parts = [
            (f"Relógio Veratus {name}", 0),
            (f" {style_title}" if style_title else "", 2),
            (f" {dial_title}" if dial_title else "", 1),
            (f" {tone_title}" if tone_title else "", 3),
        ]
    title = _fit(parts, TITLE_LIMITS[channel])

    highlights = [
        writer.ok(f"Relógio Veratus {name}, de estilo {style_text}")
        if style_text
        else writer.ok(f"Relógio Veratus {name}"),
        _bullet(look) if look else None,
        _bullet(story) if story else None,
        *(_bullet(fact) for fact in facts[:2]),
        *(
            writer.ok(_STYLE_LINES[key][1])
            for key in style_keys[:1]
            if key in _STYLE_LINES
        ),
        writer.ok(_WATCH_GIFT[1]),
    ]

    attributes: dict[str, Any] = {
        "marca": "Veratus",
        "modelo": name,
        "tipo": "relógio",
        "estilo": styles,
        "cor_do_mostrador": dial,
        "tom": tone,
        "condicao": "novo",
    }
    if sheet:
        confirmed = sheet.listing_attributes(model)
        ficha = {key: confirmed[key] for key in _SHEET_ATTRIBUTES if key in confirmed}
        if ficha:
            attributes["ficha"] = ficha
    return {
        "title": title,
        "description": description,
        "highlights": [text for text in highlights if text][:MAX_HIGHLIGHTS],
        "attributes": {key: value for key, value in attributes.items() if value},
        "facts": facts + ([color_fact] if color_fact else []),
    }


def _jewel_copy(item: dict[str, Any], writer: _Writer) -> dict[str, Any]:
    name, channel = item["name"], writer.channel
    kind, gender, usage, usage_short = _JEWEL_KIND.get(
        item.get("subcategory"), ("Peça", "feminina", None, None)
    )
    # Só "tom dourado" ou "tom prateado"; outro acabamento fica sem nome.
    tone = _JEWEL_TONE.get(item.get("finish_color"))
    match = _JEWEL_TYPE.match(str(item.get("product_type") or "").strip())
    head, tail = kind, ""
    if match:
        head = match["head"]
        if tone and match["tone"].lower() in tone:
            tail = " ".join(match["tail"].split())
    short = writer.ok(_sentence(item.get("short_description") or ""))
    story = writer.ok(_sentence(item.get("description") or ""))
    in_tone = (
        f"Peça em {tone}, da coleção Veratus Feminino."
        if tone and tone not in (story or "")
        else "Peça da coleção Veratus Feminino."
    )
    facts = [fact for fact in _confirmed_facts("feminine") if writer.fact_ok(fact)]
    gift = "Uma escolha delicada para presentear."

    description = _paragraphs(
        [
            [short, story],
            [writer.ok(usage), writer.ok(in_tone)],
            facts,
            [writer.ok(gift)],
            [
                writer.ok(f"Você recebe 1 {kind.lower()} Veratus {name}."),
                writer.ok(_QUESTION_LINE.get(channel)),
            ],
        ]
    )

    gender_title = gender.capitalize()
    tone_part = f" em {tone}" if tone else ""
    if channel == "amazon":
        parts = [
            (f"Veratus {head}", 0),
            (f" {gender_title}", 2),
            (f" {name}", 0),
            (f",{tone_part}" if tone else "", 1),
            (f" {tail}" if tail else "", 3),
        ]
    else:
        parts = [
            (head, 0),
            (f" {gender_title}", 2),
            (f" Veratus {name}", 0),
            (tone_part, 1),
            (f" {tail}" if tail and channel != "mercado_livre" else "", 3),
        ]
    title = _title_case(_fit(parts, TITLE_LIMITS[channel]))

    detail = ", ".join(filter(None, [f"Em {tone}" if tone else "", tail]))
    highlights = [
        writer.ok(f"{kind} {gender} {name}, da coleção Veratus Feminino"),
        writer.ok(detail),
        _bullet(short) if short else None,
        *(_bullet(fact) for fact in facts[:2]),
        writer.ok(usage_short),
        writer.ok(_bullet(gift)),
    ]
    attributes = {
        "marca": "Veratus",
        "modelo": name,
        "tipo": kind.lower(),
        "genero": "feminino",
        "cor": item.get("color"),
        "tom": tone,
        "condicao": "novo",
    }
    return {
        "title": title,
        "description": description,
        "highlights": [text for text in highlights if text][:MAX_HIGHLIGHTS],
        "attributes": {key: value for key, value in attributes.items() if value},
        "facts": facts,
    }


# -- fotos e bloqueios -------------------------------------------------------------


def _quarantined() -> set[str]:
    from integrations.meta_content_publisher import quarantined_media_paths

    return quarantined_media_paths()


def _photos(item: dict[str, Any], watch: bool) -> tuple[list[str], list[str]]:
    candidates = [
        item.get("primary_image"),
        *(item.get("images") or []),
        item.get("image"),
    ]
    quarantined = _quarantined()
    photos, codes = [], []
    for path in dict.fromkeys(str(path) for path in candidates if path):
        if "/" + path.lstrip("/") in quarantined:
            codes.append("MEDIA_IN_QUARANTINE")
        else:
            photos.append(path)
    if watch and item.get("image_status") != "REAL_PHOTO":
        # O criativo de vitrine não é foto do item: o anúncio espera a foto real.
        photos = []
    return photos, codes


def blocker_text(code: str, names: dict[str, str] | None = None) -> str:
    """Texto humano de um código de bloqueio."""
    if code in BLOCKER_TEXTS:
        return BLOCKER_TEXTS[code]
    if code.startswith("FICHA:"):
        from .watch_sheet import GENERAL_FIELDS, MODEL_FIELDS

        field = code.removeprefix("FICHA:")
        if "." in field:
            model, field = field.split(".", 1)
            label = MODEL_FIELDS.get(field, (None, field))[1]
            name = (names or {}).get(model)
            return f"Falta confirmar na ficha do {name or 'modelo'}: {label}."
        label = GENERAL_FIELDS.get(field, (None, field))[1]
        return f"Falta confirmar na ficha dos relógios: {label}."
    return code


def _blockers(
    product: dict[str, Any],
    item: dict[str, Any],
    channel: str,
    photos: list[str],
    photo_codes: list[str],
) -> list[str]:
    codes = []
    if item["id"] in ON_HOLD:
        codes.append("ON_HOLD")
    status = str(item.get("status") or "ACTIVE")
    if status == "DRAFT":
        codes.append("PRODUCT_DRAFT")
    elif status not in ("ACTIVE", "APPROVED"):
        codes.append("PRODUCT_INACTIVE")
    if "NEEDS_PRICING" in (product.get("status"), product.get("financial_status")):
        codes.append("NEEDS_PRICING")
    if public_price(product) is None:
        codes.append("PRICE_MISSING")
    if item.get("image_status") == "NEEDS_REAL_PHOTO":
        codes.append("NEEDS_REAL_PHOTO")
    # Relógio: foto real, origem e ficha (commercial_config.watch_channel_blockers).
    codes += commercial_config.watch_channel_blockers(item, channel)
    codes += photo_codes
    if not photos and "NEEDS_REAL_PHOTO" not in codes:
        codes.append("NO_PHOTO")
    return list(dict.fromkeys(codes))


# -- anúncio ------------------------------------------------------------------------


def listing_copy(product: dict[str, Any], channel: str) -> dict[str, Any]:
    """Anúncio de um produto do catálogo para um marketplace, para revisão.

    ``product`` é um item de ``catalog/products.json``. Só os campos públicos
    viram texto. ``ready`` só é verdadeiro sem bloqueio e sem issue do gate, e
    mesmo assim nada é publicado (``external_write`` é sempre falso).
    """
    channel = normalize_channel(channel)
    projection = public_catalog([product])
    base = {
        "id": product.get("id"),
        "sku": product.get("sku"),
        "name": product.get("name"),
        "channel": channel,
        "channel_label": CHANNEL_LABELS[channel],
        "title_limit": TITLE_LIMITS[channel],
        "external_write": False,
    }
    if not projection:
        return {
            **base,
            "collection": product.get("collection"),
            "title": "",
            "description": "",
            "highlights": [],
            "attributes": {},
            "photos": [],
            "price_brl": None,
            "blockers": [{"code": "NOT_PUBLIC", "text": BLOCKER_TEXTS["NOT_PUBLIC"]}],
            "issues": [],
            "ready": False,
        }
    item = projection[0]
    collection = item["collection"]
    writer = _Writer(channel, collection, item["name"])
    watch = commercial_config.is_watch(item)
    copy = _watch_copy(item, writer) if watch else _jewel_copy(item, writer)

    issues = []
    texts = [
        ("title", copy["title"]),
        ("description", copy["description"]),
        *(("highlights", text) for text in copy["highlights"]),
    ]
    facts = copy["facts"]
    for field, text in texts:
        issues += [f"{field}: {issue}" for issue in writer.gate(text)]
        own = text
        for fact in facts:
            own = own.replace(fact, " ").replace(_bullet(fact), " ")
        issues += [f"{field}: {issue}" for issue in writer.rules(own)]
    if len(copy["title"]) > TITLE_LIMITS[channel]:
        issues.append("title: acima do limite do canal")
    issues = list(dict.fromkeys(issues))

    photos, photo_codes = _photos(item, watch)
    codes = _blockers(product, item, channel, photos, photo_codes)
    if issues:
        codes.append("GATE_ISSUES")
    names = {item["id"]: item["name"]}
    blockers = [{"code": code, "text": blocker_text(code, names)} for code in codes]
    return {
        **base,
        "collection": collection,
        "title": copy["title"],
        "description": copy["description"],
        "highlights": copy["highlights"],
        "attributes": copy["attributes"],
        "photos": photos,
        "price_brl": public_price(product),
        "blockers": blockers,
        "issues": issues,
        "ready": not blockers,
    }


def _summary_code(code: str) -> str:
    """Códigos por modelo somam juntos: FICHA:ocean-blue.gtin → FICHA:<modelo>.gtin."""
    if code.startswith("FICHA:") and "." in code:
        return "FICHA:<modelo>." + code.split(".", 1)[1]
    return code


def readiness_summary(listings: list[dict[str, Any]]) -> dict[str, Any]:
    """Por canal: quantos prontos, quantos bloqueados e por quê."""
    summary = {}
    for channel in CHANNELS:
        items = [item for item in listings if item["channel"] == channel]
        reasons: dict[str, dict[str, Any]] = {}
        for item in items:
            for code in dict.fromkeys(
                _summary_code(blocker["code"]) for blocker in item["blockers"]
            ):
                reason = reasons.setdefault(
                    code,
                    {"code": code, "count": 0, "text": blocker_text(code)},
                )
                reason["count"] += 1
        ready = sum(1 for item in items if item["ready"])
        summary[channel] = {
            "label": CHANNEL_LABELS[channel],
            "total": len(items),
            "ready": ready,
            "blocked": len(items) - ready,
            "with_gate_issues": sum(1 for item in items if item["issues"]),
            # Mais frequente primeiro; no empate, a ordem dos bloqueios do anúncio.
            "reasons": sorted(reasons.values(), key=lambda reason: -reason["count"]),
        }
    return summary


def export_listings(products: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Todos os produtos do catálogo × os quatro marketplaces, com o resumo."""
    products = load_catalog() if products is None else products
    listings = [
        listing_copy(product, channel) for product in products for channel in CHANNELS
    ]
    return {
        "schema": "veratus.marketplace_listings/v1",
        "mode": "REVIEW",
        "external_write": False,
        "source": "catalog/products.json (campos públicos) e fatos CONFIRMED",
        "title_limits": dict(TITLE_LIMITS),
        "title_limits_status": TITLE_LIMITS_STATUS,
        "summary": readiness_summary(listings),
        "listings": listings,
    }
