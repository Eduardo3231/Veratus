"""scripts/conectar_marketplace.py against a simulated production service."""

from __future__ import annotations

import pytest

from scripts import conectar_marketplace as tool


class FakeResponse:
    def __init__(self, status: int, body: object = None) -> None:
        self.status_code = status
        self._body = body

    def json(self) -> object:
        if self._body is None:
            raise ValueError("not json")
        return self._body


class FakeSession:
    def __init__(self, routes: dict[tuple[str, str], list[FakeResponse]]) -> None:
        self.headers: dict[str, str] = {}
        self.routes = routes
        self.calls: list[tuple[str, str]] = []

    def request(self, method: str, url: str, timeout: int) -> FakeResponse:
        path = url.removeprefix(tool.BASE_URL)
        self.calls.append((method, path))
        queue = self.routes[(method, path)]
        return queue.pop(0) if len(queue) > 1 else queue[0]


STATUS = ("GET", "/integrations/mercado-livre/status")
START = ("GET", "/integrations/mercado-livre/oauth/start?format=json")
REFRESH = ("POST", "/os/connections/refresh")
AUTH_URL = "https://auth.mercadolivre.com.br/authorization?client_id=1&state=abc"


def _authorized(expires_at: str) -> FakeResponse:
    return FakeResponse(
        200,
        {"status": "authorized", "token": {"stored": True, "expires_at": expires_at}},
    )


def _execution() -> dict:
    return {
        "status": "completed",
        "execution": {
            "tasks": [
                {
                    "action": "CONSOLIDATE_MERCADO_LIVRE_READINESS",
                    "result": {
                        "read_only_connection": True,
                        "status": {
                            "account": "HEALTHY",
                            "external_category": {"external_category_name": "Relógios"},
                            "required_attributes": 4,
                            "errors": [],
                        },
                    },
                }
            ]
        },
    }


def test_connects_after_the_founder_authorizes() -> None:
    session = FakeSession(
        {
            STATUS: [
                FakeResponse(
                    200, {"status": "not_authorized", "token": {"stored": False}}
                ),
                FakeResponse(
                    200, {"status": "not_authorized", "token": {"stored": False}}
                ),
                _authorized("2026-09-29T06:00:00+00:00"),
            ],
            START: [
                FakeResponse(200, {"status": "ready", "authorization_url": AUTH_URL})
            ],
            REFRESH: [FakeResponse(200, _execution())],
        }
    )
    opened: list[str] = []

    lines = tool.connect(
        "mercado-livre",
        "segredo",
        session=session,
        open_browser=opened.append,
        poll_pause=0,
    )

    assert opened == [AUTH_URL]
    assert session.headers[tool.ADMIN_HEADER] == "segredo"
    assert "Categoria de relógios: Relógios" in lines
    assert "Prontidão: conectado em modo leitura" in lines
    assert any("desligada" in line for line in lines)
    assert session.calls[-1] == REFRESH


def test_reconnection_waits_for_a_new_token() -> None:
    old = _authorized("2026-09-28T10:00:00+00:00")
    session = FakeSession(
        {STATUS: [old, old, old, _authorized("2026-09-29T10:00:00+00:00")]}
    )
    channel = tool.CHANNELS["mercado-livre"]

    current = tool.wait_for_authorization(
        session, channel, old.json(), timeout=5, pause=0
    )

    assert current["token"]["expires_at"] == "2026-09-29T10:00:00+00:00"
    assert session.calls.count(STATUS) == 4


@pytest.mark.parametrize(
    ("routes", "message"),
    [
        (
            {STATUS: [FakeResponse(401, {"status": "error"})]},
            "Token de administrador recusado",
        ),
        (
            {STATUS: [FakeResponse(503, {"status": "blocked"})]},
            "VERATUS_TOKEN_ENCRYPTION_KEY",
        ),
        (
            {
                STATUS: [FakeResponse(200, {"status": "not_authorized", "token": {}})],
                START: [
                    FakeResponse(503, {"message": "oauth_configuration_incomplete"})
                ],
            },
            "MERCADO_LIVRE_CLIENT_ID",
        ),
    ],
)
def test_common_setup_problems_say_what_to_fix(routes, message) -> None:
    with pytest.raises(tool.ConnectionFailed, match=message):
        tool.connect(
            "mercado-livre", "x", session=FakeSession(routes), open_browser=print
        )


def test_waits_while_render_wakes_the_instance(monkeypatch) -> None:
    monkeypatch.setattr(tool.time, "sleep", lambda seconds: None)
    session = FakeSession(
        {
            STATUS: [
                FakeResponse(503, None),
                FakeResponse(503, None),
                FakeResponse(200, {}),
            ]
        }
    )

    response = tool.request(session, "GET", STATUS[1], wait_seconds=60, pause=0)

    assert response.status_code == 200 and session.calls.count(STATUS) == 3


def test_connects_a_shopee_shop_and_shows_the_store() -> None:
    status = ("GET", "/integrations/shopee/status")
    session = FakeSession(
        {
            status: [
                FakeResponse(
                    200, {"status": "not_authorized", "token": {"stored": False}}
                ),
                FakeResponse(
                    200,
                    {
                        "status": "authorized",
                        "token": {"expires_at": "2026-09-29T04:00:00+00:00"},
                    },
                ),
            ],
            ("GET", "/integrations/shopee/oauth/start?format=json"): [
                FakeResponse(
                    200, {"authorization_url": "https://partner.shopeemobile.com/x"}
                )
            ],
            ("POST", "/integrations/shopee/check"): [
                FakeResponse(
                    200,
                    {
                        "status": "completed",
                        "report": {
                            "readiness": "CONNECTED_READ_ONLY",
                            "account": "HEALTHY",
                            "account_info": {"shop_name": "Veratus", "region": "BR"},
                            "category_discovery": "REVIEW_REQUIRED",
                            "category_candidates": [
                                {"external_category_name": "Relógios de Pulso"},
                                {"external_category_name": "Relógios Esportivos"},
                            ],
                            "errors": [],
                        },
                    },
                )
            ],
        }
    )

    lines = tool.connect(
        "shopee", "t", session=session, open_browser=print, poll_pause=0
    )

    assert "Loja: Veratus (BR)" in lines
    assert "Categorias para escolher: Relógios de Pulso, Relógios Esportivos" in lines
    assert "Prontidão: conectado em modo leitura" in lines
