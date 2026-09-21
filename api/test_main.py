import sys
from types import ModuleType
from typing import ClassVar
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_list_products():
    response = client.get("/produtos")

    assert response.status_code == 200
    assert len(response.json()) == 18
    assert response.json()[0]["name"] == "Arctic White"


def test_get_product_by_id():
    response = client.get("/produtos/ocean-blue")

    assert response.status_code == 200
    assert response.json()["name"] == "Ocean Blue"


def test_get_unknown_product():
    response = client.get("/produtos/999")

    assert response.status_code == 404
    assert response.json() == {"detail": "Produto não encontrado"}


def test_gemini_requires_api_key():
    with patch.dict("os.environ", {"VERATUS_ADMIN_TOKEN": "admin-test"}, clear=True):
        response = client.post(
            "/gemini",
            headers={"X-Veratus-Admin-Token": "admin-test"},
            json={"input": "Resuma a coleção."},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "GEMINI_API_KEY não configurada"}


def test_gemini_returns_last_step():
    class FakeStep:
        def model_dump(self, mode="json"):
            return {"type": "text", "text": "Resposta do Gemini"}

    class FakeInteraction:
        steps: ClassVar = [FakeStep()]

    class FakeInteractions:
        def create(self, **kwargs):
            assert kwargs["model"] == "models/gemini-3-flash-preview"
            assert kwargs["input"] == "Pesquise a Veratus."
            assert "tools" not in kwargs
            return FakeInteraction()

    class FakeClient:
        interactions = FakeInteractions()

    class FakeGenai:
        def Client(self, **kwargs):
            assert kwargs == {"api_key": "test-key"}
            return FakeClient()

    fake_genai = FakeGenai()
    fake_google = ModuleType("google")
    fake_google.genai = fake_genai
    with (
        patch.dict(
            "os.environ",
            {"GEMINI_API_KEY": "test-key", "VERATUS_ADMIN_TOKEN": "admin-test"},
        ),
        patch.dict(sys.modules, {"google": fake_google, "google.genai": fake_genai}),
    ):
        response = client.post(
            "/gemini",
            headers={"X-Veratus-Admin-Token": "admin-test"},
            json={"input": "Pesquise a Veratus."},
        )

    assert response.status_code == 200
    assert response.json() == {"step": {"type": "text", "text": "Resposta do Gemini"}}
