"""TikTok Content Posting API: vídeo e fotos na caixa de entrada do criador.

- Só ``video.upload``: a mídia vai para a caixa de entrada do app (inbox) e o
  fundador conclui o post no TikTok, o que serve de aprovação humana. O post
  direto (``video.publish``) fica sem implementar até a auditoria do app.
- Atrás de ``TIKTOK_CONTENT_ENABLED`` (false): desligado, nenhuma chamada sai.
- O token do criador (24 h, renovável) fica cifrado (Fernet,
  ``VERATUS_TOKEN_ENCRYPTION_KEY``) na ``MarketplaceTokenStore('tiktok-content')``,
  como os demais tokens; nunca vai para log, resposta ou evento.
- Só sobe mídia do manifesto ``social/midia-aprovada.json``, fora da quarentena e
  no domínio verificado no app (``PULL_FROM_URL``).

App: ``TIKTOK_CLIENT_KEY`` e ``TIKTOK_CLIENT_SECRET``. Formato da API: a conferir
na hora de ligar.
"""

from __future__ import annotations

import os
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit

import requests

from .marketplace_clients import MarketplaceApiError

CHANNEL = "tiktok-content"
AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"
API = "https://open.tiktokapis.com"
TOKEN_PATH = "/v2/oauth/token/"
CREATOR_INFO_PATH = "/v2/post/publish/creator_info/query/"
INBOX_VIDEO_PATH = "/v2/post/publish/inbox/video/init/"
CONTENT_INIT_PATH = "/v2/post/publish/content/init/"
STATUS_PATH = "/v2/post/publish/status/fetch/"
SCOPES = "user.info.basic,video.upload"  # video.publish só depois da auditoria
CALLBACK_PATH = "/integrations/tiktok/oauth/callback"
DISABLED = {"status": "NEEDS_MANUAL", "reason": "TIKTOK_APP_AUDIT_PENDING"}


def content_enabled() -> bool:
    from .meta_webhooks import env_flag

    return env_flag("TIKTOK_CONTENT_ENABLED")


def redirect_uri() -> str:
    base = os.getenv("PUBLIC_BASE_URL", "").strip() or "https://veratus.onrender.com"
    return base.rstrip("/") + CALLBACK_PATH


def media_problems(
    urls: list[str],
    *,
    approved: dict[str, str] | None = None,
    quarantined: set[str] | None = None,
) -> list[str]:
    """Só HTTPS no domínio do site, do manifesto aprovado e fora da quarentena."""
    from integrations.meta_content_publisher import quarantined_media_paths

    from .social_scheduler import PUBLIC_BASE, load_approved_media

    approved = load_approved_media() if approved is None else approved
    quarantined = quarantined_media_paths() if quarantined is None else quarantined
    host = urlsplit(PUBLIC_BASE).netloc
    problems = []
    for url in urls:
        parts = urlsplit(url)
        if parts.path in quarantined:
            problems.append(f"MEDIA_IN_QUARANTINE: {parts.path}")
        elif parts.scheme != "https" or parts.netloc != host:
            problems.append(f"MEDIA_OUTSIDE_VERIFIED_DOMAIN: {parts.netloc}")
        elif parts.path not in approved:
            problems.append(f"MEDIA_NOT_APPROVED: {parts.path}")
    return problems


class TikTokContentClient:
    def __init__(
        self,
        client_key: str,
        client_secret: str,
        store: Any,
        *,
        session: requests.Session | None = None,
        timeout: int = 20,
    ) -> None:
        if not client_key or not client_secret:
            raise MarketplaceApiError("TIKTOK_CONFIGURATION_INCOMPLETE")
        self.client_key = client_key
        self._secret = client_secret
        self.store = store
        self.session = session or requests.Session()
        self.timeout = timeout

    @classmethod
    def from_env(
        cls, *, store: Any = None, session: requests.Session | None = None
    ) -> TikTokContentClient:
        from .mercado_livre_oauth import MarketplaceTokenStore

        return cls(
            os.getenv("TIKTOK_CLIENT_KEY", "").strip(),
            os.getenv("TIKTOK_CLIENT_SECRET", "").strip(),
            store if store is not None else MarketplaceTokenStore.from_env(CHANNEL),
            session=session,
        )

    def authorization_url(self, state: str) -> str:
        query = {
            "client_key": self.client_key,
            "scope": SCOPES,
            "response_type": "code",
            "redirect_uri": redirect_uri(),
            "state": state,
        }
        return f"{AUTHORIZE_URL}?{urlencode(query)}"

    @staticmethod
    def _require_enabled() -> None:
        if not content_enabled():
            raise MarketplaceApiError("TIKTOK_CONTENT_DISABLED")

    def _token(self, form: dict[str, str]) -> dict[str, Any]:
        self._require_enabled()
        response = self.session.post(
            f"{API}{TOKEN_PATH}",
            data={
                "client_key": self.client_key,
                "client_secret": self._secret,
                **form,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=self.timeout,
        )
        try:
            body = response.json()
        except ValueError:
            body = {}
        body = body if isinstance(body, dict) else {}
        if not response.ok or not body.get("access_token"):
            # Só o código do TikTok volta; nunca o corpo, a chave ou o token.
            error = str(body.get("error") or "token_failed")
            raise MarketplaceApiError(f"TIKTOK_{error.upper()}"[:80], http_status=401)
        return {
            "access_token": body["access_token"],
            "refresh_token": body.get("refresh_token"),
            "expires_in": body.get("expires_in"),
            "user_id": body.get("open_id"),
            "scope": body.get("scope"),
        }

    def exchange_code(self, code: str) -> Any:
        return self.store.save_tokens(
            self._token(
                {
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri(),
                }
            )
        )

    def access_token(self) -> str:
        from .mercado_livre_oauth import _utcnow

        tokens = self.store.load_tokens()
        if tokens.expires_at and tokens.expires_at <= _utcnow() + timedelta(minutes=5):
            if not tokens.refresh_token:
                raise MarketplaceApiError("REFRESH_TOKEN_MISSING", http_status=401)
            tokens = self.store.save_tokens(
                self._token(
                    {
                        "grant_type": "refresh_token",
                        "refresh_token": tokens.refresh_token,
                    }
                )
            )
        return tokens.access_token

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        self._require_enabled()
        response = self.session.post(
            f"{API}{path}",
            json=body,
            headers={
                "Authorization": f"Bearer {self.access_token()}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            timeout=self.timeout,
        )
        try:
            data = response.json()
        except ValueError:
            data = {}
        data = data if isinstance(data, dict) else {}
        code = str((data.get("error") or {}).get("code") or "")
        if not response.ok or code not in ("", "ok"):
            raise MarketplaceApiError(
                f"TIKTOK_{(code or f'HTTP_{response.status_code}').upper()}"[:80],
                http_status=response.status_code,
            )
        return data.get("data") or {}

    def creator_info(self) -> dict[str, Any]:
        """Confirma a conta conectada; nome e foto do criador não saem daqui."""
        data = self._post(CREATOR_INFO_PATH, {})
        return {
            "max_video_post_duration_sec": data.get("max_video_post_duration_sec"),
            "privacy_level_options": data.get("privacy_level_options") or [],
        }

    def upload_video(self, video_url: str) -> str:
        """Vídeo para a caixa de entrada (o TikTok baixa da URL verificada)."""
        data = self._post(
            INBOX_VIDEO_PATH,
            {"source_info": {"source": "PULL_FROM_URL", "video_url": video_url}},
        )
        return str(data.get("publish_id") or "")

    def upload_photos(self, photo_urls: list[str], *, description: str = "") -> str:
        """Fotos para a caixa de entrada (``MEDIA_UPLOAD``), nunca post direto."""
        data = self._post(
            CONTENT_INIT_PATH,
            {
                "post_info": {"description": description[:4000]},
                "source_info": {
                    "source": "PULL_FROM_URL",
                    "photo_cover_index": 0,
                    "photo_images": photo_urls,
                },
                "post_mode": "MEDIA_UPLOAD",
                "media_type": "PHOTO",
            },
        )
        return str(data.get("publish_id") or "")

    def publish_status(self, publish_id: str) -> dict[str, Any]:
        data = self._post(STATUS_PATH, {"publish_id": publish_id})
        return {"status": data.get("status"), "fail_reason": data.get("fail_reason")}


def upload_to_inbox(
    item: dict[str, Any], *, client_factory: Any = None
) -> dict[str, Any]:
    """Canal TikTok do agendador: sempre ``NEEDS_MANUAL`` (o fundador conclui no app).

    Com a chave ligada e a mídia liberada, a mídia já chega na caixa de entrada do
    TikTok; falha de envio também vira tarefa manual, sem nova tentativa.
    """
    if not content_enabled():
        return dict(DISABLED)
    media = [str(url) for url in item.get("media") or []]
    problems = media_problems(media) if media else ["MEDIA_MISSING"]
    videos = [url for url in media if urlsplit(url).path.lower().endswith(".mp4")]
    if not problems and videos and len(media) != 1:
        problems = ["TIKTOK_ONE_VIDEO_PER_POST"]
    if problems:
        return {
            "status": "NEEDS_MANUAL",
            "reason": "TIKTOK_MEDIA_BLOCKED",
            "problems": problems[:5],
        }
    try:
        client = (client_factory or TikTokContentClient.from_env)()
        publish_id = (
            client.upload_video(videos[0])
            if videos
            else client.upload_photos(media, description=str(item.get("caption") or ""))
        )
    except MarketplaceApiError as exc:
        return {"status": "NEEDS_MANUAL", "reason": exc.code}
    except RuntimeError:  # conta não conectada ou chave de cifra ausente
        return {"status": "NEEDS_MANUAL", "reason": "TIKTOK_NOT_CONNECTED"}
    except requests.RequestException:
        return {"status": "NEEDS_MANUAL", "reason": "TIKTOK_NETWORK_ERROR"}
    return {
        "status": "NEEDS_MANUAL",
        "reason": "TIKTOK_INBOX",
        "publish_id": publish_id,
    }
