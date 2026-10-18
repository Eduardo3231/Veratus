"""Consulta de CEP no fechamento: confere o CEP antes de criar o pedido.

Fontes públicas e gratuitas (do public-apis): BrasilAPI v2 e, se ela falhar, o
ViaCEP. Só o CEP sai daqui (sem nome nem endereço) e só volta cidade e UF, que
entram na resposta ao cliente. Desligado por padrão (``CEP_LOOKUP_ENABLED``);
fora do ar ou desligado, o fechamento segue como antes.
"""

from __future__ import annotations

import re
from typing import Any

import requests

from .meta_webhooks import env_flag

BRASILAPI_URL = "https://brasilapi.com.br/api/cep/v2/{cep}"
VIACEP_URL = "https://viacep.com.br/ws/{cep}/json/"
TIMEOUT_SECONDS = 4
_CEP = re.compile(r"(?<!\d)(\d{5})-?(\d{3})(?!\d)")


def find_cep(text: str) -> str | None:
    """Os 8 dígitos do primeiro CEP do texto, ou None."""
    match = _CEP.search(text or "")
    return "".join(match.groups()) if match else None


def _brasilapi(cep: str, session: Any) -> dict[str, Any]:
    response = session.get(BRASILAPI_URL.format(cep=cep), timeout=TIMEOUT_SECONDS)
    if response.status_code == 404:
        return {"status": "NOT_FOUND"}
    response.raise_for_status()
    body = response.json()
    return {"status": "FOUND", "city": body["city"], "state": body["state"]}


def _viacep(cep: str, session: Any) -> dict[str, Any]:
    response = session.get(VIACEP_URL.format(cep=cep), timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    body = response.json()
    if body.get("erro"):
        return {"status": "NOT_FOUND"}
    return {"status": "FOUND", "city": body["localidade"], "state": body["uf"]}


def lookup(
    text: str, *, session: Any = None, enabled: bool | None = None
) -> dict[str, Any]:
    """FOUND (cidade e UF), NOT_FOUND, INVALID, DISABLED ou UNAVAILABLE."""
    if not (env_flag("CEP_LOOKUP_ENABLED") if enabled is None else enabled):
        return {"status": "DISABLED"}
    cep = find_cep(text)
    if cep is None:
        return {"status": "INVALID"}
    session = session or requests.Session()
    for source in (_brasilapi, _viacep):
        try:
            return source(cep, session)
        except (requests.RequestException, ValueError, KeyError, TypeError):
            continue
    return {"status": "UNAVAILABLE"}
