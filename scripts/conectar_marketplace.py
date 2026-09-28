"""Conecta a conta de um marketplace ao Veratus em produção, em modo leitura.

Uso (PowerShell, na pasta do projeto):

    .\\.venv\\Scripts\\python.exe scripts\\conectar_marketplace.py mercado-livre
    .\\.venv\\Scripts\\python.exe scripts\\conectar_marketplace.py shopee

O comando pede o token de administrador (Render > serviço veratus-leads >
Environment > VERATUS_ADMIN_TOKEN) sem mostrá-lo na tela, abre a página de
autorização do marketplace no navegador, espera você autorizar e roda a
verificação somente leitura dos agentes. Nenhum anúncio é criado ou alterado.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
import time
import webbrowser
from typing import Any

import requests

BASE_URL = os.getenv("VERATUS_BASE_URL", "https://veratus.onrender.com").rstrip("/")
ADMIN_HEADER = "X-Veratus-Admin-Token"
CHANNELS = {
    "mercado-livre": {
        "name": "Mercado Livre",
        "status": "/integrations/mercado-livre/status",
        "start": "/integrations/mercado-livre/oauth/start?format=json",
        "readiness_action": "CONSOLIDATE_MERCADO_LIVRE_READINESS",
        "config_hint": (
            "Confira no Render (Environment) MERCADO_LIVRE_CLIENT_ID e "
            "MERCADO_LIVRE_CLIENT_SECRET, com os valores do app no Mercado Livre "
            "Developers."
        ),
        "check": "/os/connections/refresh",
    },
    "shopee": {
        "name": "Shopee",
        "status": "/integrations/shopee/status",
        "start": "/integrations/shopee/oauth/start?format=json",
        "config_hint": (
            "Confira no Render (Environment) SHOPEE_PARTNER_ID e SHOPEE_PARTNER_KEY, "
            "com os valores do app no Shopee Open Platform (App Management)."
        ),
        "check": "/integrations/shopee/check",
    },
}
ENCRYPTION_HINT = (
    "Falta VERATUS_TOKEN_ENCRYPTION_KEY no Render, ou ela é inválida. Gere uma "
    "chave nesta pasta com:\n"
    '  .\\.venv\\Scripts\\python.exe -c "from cryptography.fernet import Fernet; '
    'print(Fernet.generate_key().decode())"\n'
    "e cole em Render > veratus-leads > Environment. Guarde-a: trocar a chave "
    "invalida os tokens já salvos."
)


class ConnectionFailed(RuntimeError):
    """A step failed; the message says what to fix."""


def _json(response: requests.Response) -> dict[str, Any] | None:
    try:
        data = response.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def request(
    session: requests.Session,
    method: str,
    path: str,
    *,
    wait_seconds: float = 90,
    pause: float = 5,
) -> requests.Response:
    """Call the service, waiting while Render wakes the free instance up."""
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            response = session.request(method, BASE_URL + path, timeout=60)
            # Render answers 502-504 with an HTML page while the app starts.
            if response.status_code < 502 or _json(response) is not None:
                return response
        except requests.RequestException:
            pass
        if time.monotonic() >= deadline:
            raise ConnectionFailed(
                f"O site não respondeu em {int(wait_seconds)} s. Confira se o "
                "serviço está no ar no painel do Render e tente de novo."
            )
        time.sleep(pause)


def _status(session: requests.Session, channel: dict[str, str]) -> dict[str, Any]:
    response = request(session, "GET", channel["status"])
    data = _json(response) or {}
    if response.status_code == 401:
        raise ConnectionFailed(
            "Token de administrador recusado. Copie de novo o valor de "
            "VERATUS_ADMIN_TOKEN no Render."
        )
    if response.status_code == 503:
        raise ConnectionFailed(ENCRYPTION_HINT)
    if response.status_code != 200:
        raise ConnectionFailed(
            f"Status inesperado ({response.status_code}) ao consultar a conexão."
        )
    return data


def _authorization_url(session: requests.Session, channel: dict[str, str]) -> str:
    response = request(session, "GET", channel["start"])
    data = _json(response) or {}
    if response.status_code == 503:
        raise ConnectionFailed(
            "O app do marketplace não está configurado. " + channel["config_hint"]
        )
    url = data.get("authorization_url")
    if response.status_code != 200 or not url:
        raise ConnectionFailed(
            f"Não foi possível gerar o link de autorização ({response.status_code})."
        )
    return str(url)


def _token_marker(status: dict[str, Any]) -> tuple[Any, ...]:
    token = status.get("token") or {}
    return (status.get("status"), token.get("expires_at"))


def wait_for_authorization(
    session: requests.Session,
    channel: dict[str, str],
    before: dict[str, Any],
    *,
    timeout: float = 600,
    pause: float = 5,
) -> dict[str, Any]:
    """Poll until a new token is stored (a reconnection changes its expiry)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(pause)
        current = _status(session, channel)
        if current.get("status") == "authorized" and _token_marker(
            current
        ) != _token_marker(before):
            return current
    raise ConnectionFailed(
        "A autorização não foi concluída em 10 minutos. Rode o comando de novo; "
        "o link anterior expira."
    )


def report_summary(report: dict[str, Any]) -> list[str]:
    category = (report.get("external_category") or {}).get("external_category_name")
    shop = report.get("account_info") or {}
    lines = []
    if shop.get("shop_name"):
        lines.append(f"Loja: {shop['shop_name']} ({shop.get('region') or 'região ?'})")
    lines += [
        "Conta: "
        + ("respondendo" if report.get("account") == "HEALTHY" else "não verificada"),
        "Categoria de relógios: " + (category or "não encontrada"),
    ]
    if report.get("category_candidates"):
        names = [
            item.get("external_category_name") for item in report["category_candidates"]
        ]
        lines.append("Categorias para escolher: " + ", ".join(map(str, names[:5])))
    if report.get("required_attributes") is not None:
        lines.append(
            f"Atributos obrigatórios da categoria: {report['required_attributes']}"
        )
    lines.append(
        "Prontidão: "
        + (
            "conectado em modo leitura"
            if report.get("readiness") == "CONNECTED_READ_ONLY"
            else "ainda não pronto"
        )
    )
    if report.get("errors"):
        lines.append("Erros: " + ", ".join(map(str, report["errors"])))
    lines.append("Publicação de anúncios: desligada (nenhuma escrita no marketplace)")
    return lines


def readiness_summary(execution: dict[str, Any], action: str) -> list[str]:
    tasks = execution.get("tasks") or []
    task = next(
        (item for item in reversed(tasks) if item.get("action") == action), None
    )
    if task is None:
        return ["A verificação rodou, mas não trouxe o resultado do canal."]
    result = task.get("result") or {}
    report = dict(result.get("status") or {})
    if result.get("read_only_connection"):
        report["readiness"] = "CONNECTED_READ_ONLY"
    return report_summary(report)


def connect(
    channel_id: str,
    token: str,
    *,
    session: requests.Session | None = None,
    open_browser=webbrowser.open,
    poll_timeout: float = 600,
    poll_pause: float = 5,
) -> list[str]:
    channel = CHANNELS[channel_id]
    session = session or requests.Session()
    session.headers[ADMIN_HEADER] = token
    before = _status(session, channel)
    url = _authorization_url(session, channel)
    print(f"Abrindo a autorização do {channel['name']} no navegador.")
    print("Se não abrir, copie este link (vale 10 minutos):")
    print(url)
    open_browser(url)
    print("Aguardando você autorizar...")
    wait_for_authorization(
        session, channel, before, timeout=poll_timeout, pause=poll_pause
    )
    print("Autorizado. Rodando a verificação dos agentes (só leitura)...")
    response = request(session, "POST", channel["check"])
    data = _json(response) or {}
    if response.status_code != 200:
        raise ConnectionFailed(
            f"A verificação falhou ({response.status_code}). O token ficou salvo; "
            "rode o comando de novo para repetir a verificação."
        )
    if "readiness_action" in channel:
        return readiness_summary(
            data.get("execution") or {}, channel["readiness_action"]
        )
    return report_summary(data.get("report") or {})


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("canal", choices=sorted(CHANNELS))
    args = parser.parse_args(argv)
    token = (
        os.getenv("VERATUS_ADMIN_TOKEN", "").strip()
        or getpass.getpass(
            "Token de administrador (VERATUS_ADMIN_TOKEN do Render, não aparece na tela): "
        ).strip()
    )
    if not token:
        print("Sem token de administrador, não dá para continuar.")
        return 2
    try:
        lines = connect(args.canal, token)
    except ConnectionFailed as exc:
        print(f"\nNão conectado: {exc}")
        return 1
    print(f"\n{CHANNELS[args.canal]['name']} conectado.")
    for line in lines:
        print(f"- {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
