import hmac
import os
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

app = FastAPI(
    title="Veratus API",
    description="Primeira API educacional de produtos da Veratus.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5000",
        "http://127.0.0.1:5000",
    ],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _products() -> list[dict[str, Any]]:
    # Catálogo compartilhado com o Sales Agent. O JSON contém somente dados
    # que também aparecem na landing atual; preço e estoque real não são inventados.
    from veratus_agents.catalog import load_catalog

    return load_catalog()


class GeminiRequest(BaseModel):
    input: str = Field(min_length=1, max_length=4000)


def require_admin_token(
    x_veratus_admin_token: str | None = Header(default=None),
) -> None:
    expected = os.getenv("VERATUS_ADMIN_TOKEN", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="Endpoint interno não configurado")
    if not x_veratus_admin_token or not hmac.compare_digest(
        x_veratus_admin_token, expected
    ):
        raise HTTPException(status_code=401, detail="Não autorizado")


def _gemini_step_value(step: Any) -> Any:
    if hasattr(step, "model_dump"):
        return step.model_dump(mode="json")
    if isinstance(step, dict):
        return step
    return str(step)


def generate_with_gemini(user_input: str) -> Any:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=503, detail="GEMINI_API_KEY não configurada")

    try:
        from google import genai
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="Dependência google-genai não instalada",
        ) from exc

    client = genai.Client(api_key=api_key)
    interaction = client.interactions.create(
        model=os.getenv("GEMINI_MODEL", "models/gemini-3-flash-preview"),
        input=user_input,
        generation_config={
            "temperature": 0.3,
            "max_output_tokens": 2048,
            "top_p": 0.9,
        },
    )
    if not interaction.steps:
        raise HTTPException(status_code=502, detail="Gemini não retornou uma resposta")
    return _gemini_step_value(interaction.steps[-1])


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/produtos")
def list_products() -> list[dict[str, Any]]:
    return _products()


@app.get("/api/catalog")
def public_product_catalog() -> list[dict[str, Any]]:
    from veratus_agents.catalog import public_catalog

    return public_catalog(_products())


@app.get("/produtos/{product_id}")
def get_product(product_id: str) -> dict[str, Any]:
    product = next(
        (item for item in _products() if item["id"] == product_id),
        None,
    )
    if product is None:
        raise HTTPException(status_code=404, detail="Produto não encontrado")
    return product


@app.post("/gemini", dependencies=[Depends(require_admin_token)])
def ask_gemini(request: GeminiRequest) -> dict[str, Any]:
    return {"step": generate_with_gemini(request.input)}
