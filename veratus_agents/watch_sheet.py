"""Ficha dos relógios: o que o fundador preenche e o que cada canal exige.

A ficha fica em ``catalog/ficha-relogios.yaml``. Cada campo tem ``valor`` e
``status`` (``PENDENTE`` ou ``CONFIRMADO``); só o confirmado é usado. Ao
confirmar um campo, o relógio é liberado sozinho no que dependia dele, sem
mudança de código:

- ``envio_do_brasil: true`` libera os marketplaces, que exigem envio de dentro
  do Brasil (``ORIGIN_UNCONFIRMED`` sai do rascunho e da economia por canal);
- material, movimento, resistência à água, medidas e garantia viram fatos que o
  atendimento pode afirmar e que o gate aceita;
- cores, código de barras, estoque e embalagem completam o anúncio.

A foto real continua entrando pelo importador (``incoming/relogios/<id>.jpg``):
sem ela, o relógio segue à venda no site e nas conversas, mas não vai a post,
anúncio pago ou marketplace.

``python -m veratus_agents.watch_sheet status`` mostra o que falta, por modelo
e por canal.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[1]
SHEET_PATH = BASE_DIR / "catalog" / "ficha-relogios.yaml"
SCHEMA_ID = "veratus.watch_sheet/v1"
STATUSES = ("PENDENTE", "CONFIRMADO")

# campo → (tipo, rótulo)
GENERAL_FIELDS: dict[str, tuple[str, str]] = {
    "envio_do_brasil": (
        "bool",
        "envio de dentro do Brasil (exigido pelos marketplaces)",
    ),
    "cidade_de_envio": ("text", "cidade de envio"),
    "prazo_postagem_dias_uteis": ("int", "prazo de postagem em dias úteis"),
    "material_caixa": ("text", "material da caixa"),
    "material_pulseira": ("text", "material da pulseira"),
    "movimento": ("text", "movimento"),
    "resistencia_agua": ("text", "resistência à água"),
    "diametro_caixa_mm": ("number", "diâmetro da caixa (mm)"),
    "espessura_caixa_mm": ("number", "espessura da caixa (mm)"),
    "largura_pulseira_mm": ("number", "largura da pulseira (mm)"),
    "garantia": ("text", "garantia"),
    "embalagem_peso_g": ("int", "peso do pacote (g)"),
    "embalagem_comprimento_cm": ("number", "comprimento do pacote (cm)"),
    "embalagem_largura_cm": ("number", "largura do pacote (cm)"),
    "embalagem_altura_cm": ("number", "altura do pacote (cm)"),
}
MODEL_FIELDS: dict[str, tuple[str, str]] = {
    "cor_mostrador": ("text", "cor do mostrador"),
    "cor_pulseira": ("text", "cor da pulseira"),
    "gtin": ("gtin", "código de barras (EAN/GTIN ou ISENTO)"),
    "estoque": ("int", "estoque para os marketplaces"),
}

MARKETPLACES = ("mercado_livre", "shopee", "tiktok_shop", "amazon")
# Canais onde o relógio já está à venda (preço e pedido confirmados).
OPEN_CHANNELS = ("site", "whatsapp", "instagram_direct")
PHOTO_CHANNELS = ("instagram_post", "facebook_post", "meta_ads")
CHANNELS = (*OPEN_CHANNELS, *PHOTO_CHANNELS, *MARKETPLACES)
MARKETPLACE_GENERAL = (
    "envio_do_brasil",
    "cidade_de_envio",
    "prazo_postagem_dias_uteis",
    "material_caixa",
    "material_pulseira",
    "movimento",
    "diametro_caixa_mm",
    "embalagem_peso_g",
    "embalagem_comprimento_cm",
    "embalagem_largura_cm",
    "embalagem_altura_cm",
)
MARKETPLACE_MODEL = ("cor_mostrador", "cor_pulseira", "estoque")
CHANNEL_ALIASES = {
    "mercado-livre": "mercado_livre",
    "tiktok-shop": "tiktok_shop",
    "meta": "instagram_post",
    "instagram": "instagram_post",
    "facebook": "facebook_post",
}


class WatchSheetError(ValueError):
    """A ficha está fora do formato; a mensagem diz qual campo corrigir."""


@dataclass(frozen=True)
class Entry:
    value: Any
    confirmed: bool


def _coerce(kind: str, value: Any, where: str) -> Any:
    if value is None:
        return None
    if kind == "bool":
        if not isinstance(value, bool):
            raise WatchSheetError(f"{where}: use true ou false")
        return value
    if kind == "text":
        text = " ".join(str(value).split())
        if not text or len(text) > 120:
            raise WatchSheetError(f"{where}: texto vazio ou longo demais")
        return text
    if kind == "gtin":
        text = str(value).strip().upper()
        if text == "ISENTO" or (text.isdigit() and len(text) in (8, 12, 13, 14)):
            return text
        raise WatchSheetError(
            f"{where}: código de barras com 8, 12, 13 ou 14 dígitos, ou ISENTO"
        )
    try:
        number = Decimal(str(value).replace(",", "."))
    except InvalidOperation as exc:
        raise WatchSheetError(f"{where}: número inválido") from exc
    if not number.is_finite() or number < 0:
        raise WatchSheetError(f"{where}: número inválido")
    if kind == "int":
        if number != number.to_integral_value():
            raise WatchSheetError(f"{where}: use número inteiro")
        return int(number)
    return number


def _entries(
    raw: Any, fields: dict[str, tuple[str, str]], where: str
) -> dict[str, Entry]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise WatchSheetError(f"{where}: precisa ser um bloco de campos")
    unknown = set(raw) - set(fields)
    if unknown:
        raise WatchSheetError(f"{where}: campo desconhecido {sorted(unknown)}")
    entries = {}
    for name, (kind, _label) in fields.items():
        item = raw.get(name) or {"valor": None, "status": "PENDENTE"}
        if not isinstance(item, dict) or set(item) - {"valor", "status"}:
            raise WatchSheetError(f"{where}.{name}: use valor e status")
        status = str(item.get("status") or "PENDENTE").strip().upper()
        if status not in STATUSES:
            raise WatchSheetError(f"{where}.{name}: status é PENDENTE ou CONFIRMADO")
        value = _coerce(kind, item.get("valor"), f"{where}.{name}")
        if status == "CONFIRMADO" and value is None:
            raise WatchSheetError(f"{where}.{name}: CONFIRMADO sem valor")
        entries[name] = Entry(value, status == "CONFIRMADO")
    return entries


def _number(value: Any) -> str:
    """41 → "41"; 12.5 → "12,5" (como o cliente lê)."""
    text = f"{Decimal(str(value)).normalize():f}"
    return text.replace(".", ",")


class WatchSheet:
    def __init__(
        self, general: dict[str, Entry], models: dict[str, dict[str, Entry]]
    ) -> None:
        self.general = general
        self.models = models

    # -- leitura ---------------------------------------------------------------

    def entry(self, name: str, model: str | None = None) -> Entry:
        if name in MODEL_FIELDS:
            return (self.models.get(model or "") or {}).get(name) or Entry(None, False)
        return self.general.get(name) or Entry(None, False)

    def confirmed(self, name: str, model: str | None = None) -> Any:
        entry = self.entry(name, model)
        return entry.value if entry.confirmed else None

    def origin_confirmed(self) -> bool:
        return self.confirmed("envio_do_brasil") is True

    # -- o que falta -----------------------------------------------------------

    def missing(self, model: str, channel: str, *, real_photo: bool) -> list[str]:
        """Códigos do que falta para o modelo entrar no canal (vazio = liberado)."""
        channel = CHANNEL_ALIASES.get(channel, channel)
        if channel in OPEN_CHANNELS:
            return []
        missing = [] if real_photo else ["NEEDS_REAL_PHOTO"]
        if channel not in MARKETPLACES:
            return missing
        if not self.origin_confirmed():
            missing.append("ORIGIN_UNCONFIRMED")
        for name in MARKETPLACE_GENERAL[1:]:
            if self.confirmed(name) is None:
                missing.append(f"FICHA:{name}")
        model_fields = (
            (*MARKETPLACE_MODEL, "gtin") if channel == "amazon" else (MARKETPLACE_MODEL)
        )
        for name in model_fields:
            if self.confirmed(name, model) is None:
                missing.append(f"FICHA:{model}.{name}")
        return missing

    def pending_general(self) -> list[str]:
        return [name for name, entry in self.general.items() if not entry.confirmed]

    # -- fatos para o atendimento e o gate ---------------------------------------

    def sales_facts(self, names: dict[str, str] | None = None) -> dict[str, dict]:
        """Fatos CONFIRMADOS, no formato de CONFIRMED_SALES_FACTS.

        ``gate_issue`` diz qual bloqueio do gate o fato libera, e só para o texto
        dele (por exemplo, "41 mm" libera "41 mm", não "40 mm").
        """
        facts: dict[str, dict[str, str]] = {}

        def add(ref: str, claim_type: str, text: str, gate_issue: str | None) -> None:
            fact = {"claim_type": claim_type, "scope": "watches", "text": text}
            if gate_issue:
                fact["gate_issue"] = gate_issue
            facts[ref] = fact

        city = self.confirmed("cidade_de_envio")
        if city and self.origin_confirmed():
            add(
                "relogios-envio",
                "shipping",
                f"Os relógios são enviados de {city}.",
                None,
            )
        days = self.confirmed("prazo_postagem_dias_uteis")
        if days is not None:
            add(
                "relogios-postagem",
                "delivery",
                f"Postagem em até {days} dias úteis após o pagamento.",
                None,
            )
        for name, ref, template, issue in (
            (
                "material_caixa",
                "relogios-material-caixa",
                "Caixa em {}.",
                "material não validado",
            ),
            (
                "material_pulseira",
                "relogios-material-pulseira",
                "Pulseira em {}.",
                "material não validado",
            ),
            (
                "movimento",
                "relogios-movimento",
                "Movimento {}.",
                "especificação técnica não validada",
            ),
            (
                "resistencia_agua",
                "relogios-resistencia-agua",
                "Resistência à água: {}.",
                "resistência à água não validada",
            ),
            (
                "garantia",
                "relogios-garantia",
                "Garantia de {}.",
                "garantia não validada",
            ),
        ):
            value = self.confirmed(name)
            if value:
                claim = "warranty" if name == "garantia" else "specification"
                add(ref, claim, template.format(value), issue)
        for name, ref, template in (
            ("diametro_caixa_mm", "relogios-diametro", "Caixa de {} mm de diâmetro."),
            (
                "espessura_caixa_mm",
                "relogios-espessura",
                "Caixa de {} mm de espessura.",
            ),
            (
                "largura_pulseira_mm",
                "relogios-largura-pulseira",
                "Pulseira de {} mm de largura.",
            ),
        ):
            value = self.confirmed(name)
            if value is not None:
                add(
                    ref,
                    "specification",
                    template.format(_number(value)),
                    "medida não validada",
                )
        for model, entries in self.models.items():
            dial = (
                entries["cor_mostrador"].value
                if entries["cor_mostrador"].confirmed
                else None
            )
            strap = (
                entries["cor_pulseira"].value
                if entries["cor_pulseira"].confirmed
                else None
            )
            if dial and strap:
                name = (names or {}).get(model, model)
                add(
                    f"{model}-cores",
                    "specification",
                    f"O {name} tem mostrador em tom {dial.lower()} e pulseira em tom "
                    f"{strap.lower()}.",
                    None,
                )
        return facts

    def pending_texts(self) -> list[str]:
        """O que o atendimento ainda responde com "a equipe confirma"."""
        labels = {
            "material_caixa": "Material da caixa dos relógios.",
            "material_pulseira": "Material da pulseira dos relógios.",
            "movimento": "Movimento dos relógios.",
            "resistencia_agua": "Resistência à água dos relógios.",
            "diametro_caixa_mm": "Medidas dos relógios.",
            "garantia": "Garantia dos relógios.",
            "cidade_de_envio": "De onde os relógios são enviados.",
        }
        return [text for name, text in labels.items() if self.confirmed(name) is None]

    def listing_attributes(self, model: str) -> dict[str, Any]:
        """Atributos confirmados para o rascunho de marketplace."""
        attributes = {}
        for name in GENERAL_FIELDS:
            value = self.confirmed(name)
            if value is not None:
                attributes[name] = str(value) if isinstance(value, Decimal) else value
        for name in MODEL_FIELDS:
            value = self.confirmed(name, model)
            if value is not None:
                attributes[name] = value
        return attributes


def load_sheet(
    path: str | Path | None = None, *, models: list[str] | None = None
) -> WatchSheet:
    import yaml

    path = Path(path or os.getenv("WATCH_SHEET_PATH", "").strip() or SHEET_PATH)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise WatchSheetError(f"{path.name}: ilegível") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA_ID:
        raise WatchSheetError(f"{path.name}: schema deve ser {SCHEMA_ID}")
    general = _entries(data.get("geral"), GENERAL_FIELDS, "geral")
    raw_models = data.get("modelos") or {}
    if not isinstance(raw_models, dict):
        raise WatchSheetError("modelos: precisa ser um bloco por modelo")
    known = set(models if models is not None else _watch_ids())
    unknown = set(raw_models) - known
    if unknown:
        raise WatchSheetError(f"modelos: id desconhecido {sorted(unknown)}")
    parsed = {
        model: _entries(raw_models.get(model), MODEL_FIELDS, f"modelos.{model}")
        for model in sorted(known)
    }
    return WatchSheet(general, parsed)


def _watch_catalog() -> list[dict[str, Any]]:
    from .catalog import load_catalog

    return [
        product
        for product in load_catalog()
        if (product.get("collection") or "watches") == "watches"
    ]


def _watch_ids() -> list[str]:
    return [product["id"] for product in _watch_catalog()]


@lru_cache(maxsize=2)
def _cached(path: str, mtime: float) -> WatchSheet:
    return load_sheet(path)


def default_sheet() -> WatchSheet | None:
    """A ficha do repositório; ``None`` se faltar ou estiver fora do formato."""
    path = Path(os.getenv("WATCH_SHEET_PATH", "").strip() or SHEET_PATH)
    try:
        return _cached(str(path), path.stat().st_mtime)
    except (OSError, WatchSheetError, ImportError):
        return None


def model_id(product: dict[str, Any]) -> str:
    from .meta_webhooks import normalize_text

    for candidate in (product.get("id"), product.get("name")):
        if candidate:
            slug = "-".join(normalize_text(str(candidate)).split())
            if slug in _watch_ids():
                return slug
    return str(product.get("id") or product.get("sku") or "")


# -- relatório -------------------------------------------------------------------


def readiness_report(
    sheet: WatchSheet | None = None, *, fee_config: Any = None
) -> dict[str, Any]:
    """Por modelo e por canal: liberado ou o que falta preencher."""
    sheet = sheet or load_sheet()
    catalog = _watch_catalog()
    models = {}
    ready = dict.fromkeys(CHANNELS, 0)
    for product in catalog:
        real_photo = product.get("image_status") == "REAL_PHOTO"
        channels = {}
        for channel in CHANNELS:
            missing = sheet.missing(product["id"], channel, real_photo=real_photo)
            channels[channel] = missing
            ready[channel] += not missing
        models[product["id"]] = {
            "name": product["name"],
            "real_photo": real_photo,
            "channels": channels,
        }
    economics = {}
    try:
        from . import channel_economics as engine
        from .commercial_config import OFFICIAL_SALE_PRICE_BRL

        config = fee_config or engine.default_config()
        for channel in config.channels:
            result = engine.evaluate(
                channel,
                "watch",
                OFFICIAL_SALE_PRICE_BRL,
                config=config,
                require_cost=False,
                assumptions=engine.Assumptions.from_env(
                    watch_origin_confirmed=sheet.origin_confirmed()
                ),
            )
            economics[channel] = {
                "status": result["status"],
                "pending": result["status_reasons"],
            }
    except Exception as exc:  # noqa: BLE001 - o relatório sai mesmo sem taxas
        economics = {"error": type(exc).__name__}
    return {
        "sheet": "catalog/ficha-relogios.yaml",
        "general_pending": sheet.pending_general(),
        "models": models,
        "ready_by_channel": {
            channel: f"{count}/{len(catalog)}" for channel, count in ready.items()
        },
        "economics": economics,
    }


def _short(code: str) -> str:
    if code == "NEEDS_REAL_PHOTO":
        return "foto real"
    name = code.removeprefix("FICHA:").split(".")[-1]
    return f"{name} (só Amazon)" if name == "gtin" else name


def render_status(report: dict[str, Any]) -> str:
    lines = ["Ficha dos relógios — o que falta preencher", ""]
    lines.append("Geral (vale para os 9 modelos):")
    for name in report["general_pending"]:
        lines.append(f"  [ ] {name} — {GENERAL_FIELDS[name][1]}")
    if not report["general_pending"]:
        lines.append("  tudo confirmado")
    lines += ["", "Por modelo:"]
    for model in report["models"].values():
        missing = sorted(
            {
                code
                for codes in model["channels"].values()
                for code in codes
                if code == "NEEDS_REAL_PHOTO" or code.count(".") == 1
            },
            key=lambda code: (code != "NEEDS_REAL_PHOTO", code),
        )
        lines.append(
            f"  {model['name']}: {' · '.join(_short(code) for code in missing) or 'nada'}"
        )
    lines += ["", "Modelos liberados por canal:"]
    for channel, count in report["ready_by_channel"].items():
        lines.append(f"  {channel}: {count}")
    lines += ["", "Economia por canal (taxas em config/channel_fees_2026-10.json):"]
    for channel, item in report["economics"].items():
        if isinstance(item, dict):
            lines.append(f"  {channel}: {item['status']}")
    lines += [
        "",
        "Como liberar:",
        "  1. Em catalog/ficha-relogios.yaml, preencha `valor` e troque o status",
        "     para CONFIRMADO.",
        "  2. Fotos: salve incoming/relogios/<id>.jpg e rode",
        "     python scripts/import_watch_photos.py --sem-marca-de-terceiros",
        "  3. Rode de novo: python -m veratus_agents.watch_sheet status",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m veratus_agents.watch_sheet")
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status", help="o que falta, por modelo e por canal")
    status.add_argument("--json", action="store_true")
    sub.add_parser("check", help="só valida a ficha")
    args = parser.parse_args(argv)
    try:
        sheet = load_sheet()
    except WatchSheetError as exc:
        print(f"Ficha com erro: {exc}")
        return 2
    if args.command == "check":
        print("Ficha válida.")
        return 0
    report = readiness_report(sheet)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(render_status(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
