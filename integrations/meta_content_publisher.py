#!/usr/bin/env python3
"""Valida e publica a fila social da Veratus pela API oficial da Meta."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PAYLOADS = ROOT / "social" / "publishing-payloads.json"
DEFAULT_STATE = ROOT / "social" / "publishing-state.json"
GRAPH_API_VERSION = os.getenv("META_GRAPH_API_VERSION", "v25.0").strip()
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Variável ausente: {name}")
    return value


def load_payloads(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("O arquivo de publicações precisa conter uma lista.")
    orders = [item.get("order") for item in data]
    if len(orders) != len(set(orders)):
        raise ValueError("Há números de ordem repetidos.")
    return data


def select_payload(payloads: list[dict[str, Any]], order: int) -> dict[str, Any]:
    for item in payloads:
        if item.get("order") == order:
            return item
    raise ValueError(f"Publicação {order} não encontrada.")


def validate_payload(item: dict[str, Any], *, check_url: bool = True) -> list[str]:
    errors: list[str] = []
    media_type = item.get("type")
    if media_type not in {"IMAGE", "REELS"}:
        errors.append("type deve ser IMAGE ou REELS")
    if not str(item.get("caption", "")).strip():
        errors.append("caption ausente")
    media_url = str(item.get("media_url", ""))
    if not media_url.startswith("https://"):
        errors.append("media_url precisa usar HTTPS")
    if media_type == "IMAGE" and not media_url.lower().endswith((".jpg", ".jpeg")):
        errors.append("imagem precisa ser JPEG")
    if media_type == "REELS" and not media_url.lower().endswith(".mp4"):
        errors.append("Reel precisa ser MP4")
    if len(str(item.get("caption", ""))) > 2200:
        errors.append("caption ultrapassa 2.200 caracteres")
    if check_url and not errors:
        response = requests.get(media_url, stream=True, timeout=30)
        try:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if media_type == "IMAGE" and "image/jpeg" not in content_type:
                errors.append(f"Content-Type inesperado: {content_type}")
            if media_type == "REELS" and "video/mp4" not in content_type:
                errors.append(f"Content-Type inesperado: {content_type}")
        finally:
            response.close()
    return errors


def create_container(
    session: requests.Session,
    item: dict[str, Any],
    ig_user_id: str,
    access_token: str,
) -> str:
    params: dict[str, Any] = {
        "caption": item["caption"],
        "access_token": access_token,
    }
    if item["type"] == "IMAGE":
        params["image_url"] = item["media_url"]
        if item.get("alt_text"):
            params["alt_text"] = item["alt_text"]
    else:
        params.update(
            {
                "media_type": "REELS",
                "video_url": item["media_url"],
                "share_to_feed": "true",
            }
        )
        if item.get("cover_url"):
            params["cover_url"] = item["cover_url"]

    response = session.post(
        f"{GRAPH_BASE}/{ig_user_id}/media",
        data=params,
        timeout=60,
    )
    response.raise_for_status()
    return str(response.json()["id"])


def wait_until_ready(
    session: requests.Session,
    container_id: str,
    access_token: str,
    *,
    attempts: int = 30,
    interval: int = 4,
) -> None:
    for _ in range(attempts):
        response = session.get(
            f"{GRAPH_BASE}/{container_id}",
            params={"fields": "status_code,status", "access_token": access_token},
            timeout=30,
        )
        response.raise_for_status()
        status = response.json()
        code = status.get("status_code")
        if code == "FINISHED":
            return
        if code in {"ERROR", "EXPIRED"}:
            raise RuntimeError(f"Contêiner recusado pela Meta: {status.get('status', code)}")
        time.sleep(interval)
    raise TimeoutError("A Meta ainda não concluiu o processamento do arquivo.")


def publish_container(
    session: requests.Session,
    container_id: str,
    ig_user_id: str,
    access_token: str,
) -> str:
    response = session.post(
        f"{GRAPH_BASE}/{ig_user_id}/media_publish",
        data={"creation_id": container_id, "access_token": access_token},
        timeout=60,
    )
    response.raise_for_status()
    return str(response.json()["id"])


def read_publication(
    session: requests.Session,
    media_id: str,
    access_token: str,
) -> dict[str, Any]:
    response = session.get(
        f"{GRAPH_BASE}/{media_id}",
        params={
            "fields": "id,permalink,media_type,timestamp",
            "access_token": access_token,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def save_state(path: Path, order: int, publication: dict[str, Any]) -> None:
    state: dict[str, Any] = {}
    if path.exists():
        state = json.loads(path.read_text(encoding="utf-8"))
    state[str(order)] = publication
    path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payloads", type=Path, default=DEFAULT_PAYLOADS)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--order", type=int, help="Número da publicação.")
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Publica de verdade. Sem esta opção, apenas valida.",
    )
    parser.add_argument(
        "--skip-url-check",
        action="store_true",
        help="Não consulta os arquivos públicos durante a validação.",
    )
    args = parser.parse_args()

    payloads = load_payloads(args.payloads)
    selected = payloads if args.order is None else [select_payload(payloads, args.order)]
    failed = False
    for item in selected:
        errors = validate_payload(item, check_url=not args.skip_url_check)
        label = f"{item['order']} — {item['name']}"
        if errors:
            failed = True
            print(f"ERRO {label}: {'; '.join(errors)}")
        else:
            print(f"OK {label}: {item['type']} · {item['media_url']}")
    if failed:
        return 1
    if not args.publish:
        print("Validação concluída. Nenhuma publicação foi enviada.")
        return 0
    if args.order is None:
        raise ValueError("Use --order junto com --publish para enviar uma peça por vez.")

    access_token = required_env("META_PAGE_ACCESS_TOKEN")
    ig_user_id = required_env("META_IG_BUSINESS_ACCOUNT_ID")
    item = selected[0]
    with requests.Session() as session:
        container_id = create_container(session, item, ig_user_id, access_token)
        wait_until_ready(session, container_id, access_token)
        media_id = publish_container(session, container_id, ig_user_id, access_token)
        publication = read_publication(session, media_id, access_token)
    save_state(args.state, args.order, publication)
    print(json.dumps(publication, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except requests.HTTPError as exc:
        body = exc.response.text[:800] if exc.response is not None else ""
        print(f"Falha na API da Meta: {exc}. {body}", file=sys.stderr)
        raise SystemExit(1)
    except (ValueError, RuntimeError, TimeoutError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
