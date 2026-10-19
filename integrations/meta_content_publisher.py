#!/usr/bin/env python3
"""Chamadas da API oficial da Meta usadas pelo agendador social.

Publicar sai só pelo agendador (``python -m veratus_agents.social_scheduler
run-due``), com ``SOCIAL_PUBLISH_ENABLED`` e a aprovação semanal do fundador.
Esta CLI só valida um arquivo de payloads antigo; ela não publica.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


ROOT = Path(__file__).resolve().parents[1]
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
        raise TypeError("O arquivo de publicações precisa conter uma lista.")
    orders = [item.get("order") for item in data]
    if len(orders) != len(set(orders)):
        raise ValueError("Há números de ordem repetidos.")
    return data


def select_payload(payloads: list[dict[str, Any]], order: int) -> dict[str, Any]:
    for item in payloads:
        if item.get("order") == order:
            return item
    raise ValueError(f"Publicação {order} não encontrada.")


QUARANTINE_MANIFEST = ROOT / "quarantine" / "third-party-marks" / "manifest.json"


def quarantined_media_paths() -> set[str]:
    """Public URL paths of media removed for showing third-party marks."""
    if not QUARANTINE_MANIFEST.exists():
        return set()
    manifest = json.loads(QUARANTINE_MANIFEST.read_text(encoding="utf-8"))
    return {
        "/" + path.removeprefix("landing/")
        for path in manifest["original_paths"]
        if path.startswith("landing/")
    }


def validate_payload(item: dict[str, Any], *, check_url: bool = True) -> list[str]:
    errors: list[str] = []
    if urlsplit(str(item.get("media_url", ""))).path in quarantined_media_paths():
        # Checked before any HTTP call: the old URL may still resolve until deploy.
        return ["mídia em quarentena: mostra marca de terceiros"]
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


def _post_media(
    session: requests.Session, ig_user_id: str, params: dict[str, Any]
) -> str:
    response = session.post(f"{GRAPH_BASE}/{ig_user_id}/media", data=params, timeout=60)
    response.raise_for_status()
    return str(response.json()["id"])


def create_social_container(
    session: requests.Session,
    item: dict[str, Any],
    ig_user_id: str,
    access_token: str,
) -> str:
    """Contêiner do agendador: IMAGE, CAROUSEL (até 10 imagens), STORY ou REEL.

    ``item`` traz ``type``, ``caption``, ``alt_text`` e ``media_urls``; Reel e
    Story em vídeo trazem ``video_url`` (e o Reel, ``cover_url`` opcional).
    """
    media_type, urls = item["type"], list(item["media_urls"])
    video_url = item.get("video_url")
    if media_type == "REEL":
        if not video_url:
            raise ValueError("Reel precisa de um MP4.")
        params = {
            "media_type": "REELS",
            "video_url": video_url,
            "caption": item["caption"],
            "share_to_feed": "true",
            "access_token": access_token,
        }
        if item.get("cover_url"):
            params["cover_url"] = item["cover_url"]
        return _post_media(session, ig_user_id, params)
    if media_type == "IMAGE":
        params = {
            "image_url": urls[0],
            "caption": item["caption"],
            "access_token": access_token,
        }
        if item.get("alt_text"):
            params["alt_text"] = item["alt_text"]
        return _post_media(session, ig_user_id, params)
    if media_type == "STORY":
        source = {"video_url": video_url} if video_url else {"image_url": urls[0]}
        return _post_media(
            session,
            ig_user_id,
            {**source, "media_type": "STORIES", "access_token": access_token},
        )
    if media_type == "CAROUSEL":
        if not 2 <= len(urls) <= 10:
            raise ValueError("Carrossel precisa de 2 a 10 imagens.")
        children = [
            _post_media(
                session,
                ig_user_id,
                {
                    "image_url": url,
                    "is_carousel_item": "true",
                    "access_token": access_token,
                },
            )
            for url in urls
        ]
        for child in children:
            wait_until_ready(session, child, access_token)
        return _post_media(
            session,
            ig_user_id,
            {
                "media_type": "CAROUSEL",
                "children": ",".join(children),
                "caption": item["caption"],
                "access_token": access_token,
            },
        )
    raise ValueError(f"Formato não suportado pelo agendador: {media_type}")


def publish_facebook_page(
    session: requests.Session,
    item: dict[str, Any],
    page_id: str,
    access_token: str,
) -> str:
    """Post na Página do Facebook: foto única ou várias fotos num post."""
    urls = list(item["media_urls"])
    if len(urls) == 1:
        response = session.post(
            f"{GRAPH_BASE}/{page_id}/photos",
            data={
                "url": urls[0],
                "caption": item["caption"],
                "published": "true",
                "access_token": access_token,
            },
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        return str(data.get("post_id") or data["id"])
    photo_ids = []
    for url in urls[:10]:
        response = session.post(
            f"{GRAPH_BASE}/{page_id}/photos",
            data={"url": url, "published": "false", "access_token": access_token},
            timeout=60,
        )
        response.raise_for_status()
        photo_ids.append(str(response.json()["id"]))
    response = session.post(
        f"{GRAPH_BASE}/{page_id}/feed",
        data={
            "message": item["caption"],
            "attached_media": json.dumps(
                [{"media_fbid": photo_id} for photo_id in photo_ids]
            ),
            "access_token": access_token,
        },
        timeout=60,
    )
    response.raise_for_status()
    return str(response.json()["id"])


def publish_facebook_video(
    session: requests.Session,
    item: dict[str, Any],
    page_id: str,
    access_token: str,
) -> str:
    """Vídeo na Página (``/{page_id}/videos`` com ``file_url``).

    Campos a conferir na hora de ligar: a Meta pode pedir o fluxo de Reels da
    Página (``/{page_id}/video_reels``) para vídeo vertical.
    """
    response = session.post(
        f"{GRAPH_BASE}/{page_id}/videos",
        data={
            "file_url": item["video_url"],
            "description": item["caption"],
            "published": "true",
            "access_token": access_token,
        },
        timeout=120,
    )
    response.raise_for_status()
    return str(response.json()["id"])


def read_facebook_permalink(
    session: requests.Session, post_id: str, access_token: str
) -> str | None:
    response = session.get(
        f"{GRAPH_BASE}/{post_id}",
        params={"fields": "permalink_url", "access_token": access_token},
        timeout=30,
    )
    response.raise_for_status()
    return response.json().get("permalink_url")


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
            raise RuntimeError(
                f"Contêiner recusado pela Meta: {status.get('status', code)}"
            )
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payloads", type=Path, help="Arquivo de payloads a validar.")
    parser.add_argument("--order", type=int, help="Número da publicação.")
    parser.add_argument("--publish", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--skip-url-check",
        action="store_true",
        help="Não consulta os arquivos públicos durante a validação.",
    )
    args = parser.parse_args(argv)
    if args.publish:
        # Publicar fora do agendador pularia a flag e a aprovação semanal.
        print(
            "Esta CLI não publica. Use: python -m veratus_agents.social_scheduler "
            "validate <calendario.json>, e o run-due com SOCIAL_PUBLISH_ENABLED "
            "e a aprovação da semana.",
            file=sys.stderr,
        )
        return 2
    if args.payloads is None:
        print(
            "Nada a validar: o calendário do agendador se confere com "
            "python -m veratus_agents.social_scheduler validate <arquivo>."
        )
        return 0
    payloads = load_payloads(args.payloads)
    selected = (
        payloads if args.order is None else [select_payload(payloads, args.order)]
    )
    failed = False
    for item in selected:
        errors = validate_payload(item, check_url=not args.skip_url_check)
        label = f"{item['order']} — {item['name']}"
        if errors:
            failed = True
            print(f"ERRO {label}: {'; '.join(errors)}")
        else:
            print(f"OK {label}: {item['type']} · {item['media_url']}")
    return 1 if failed else 0


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
