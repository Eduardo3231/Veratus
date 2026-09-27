"""Identificação do vendedor: contato público e CPF só pela variável de ambiente."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from integrations.webhook import app

ROOT = Path(__file__).resolve().parents[1]
CPF_SHAPE = re.compile(r"(?<!\d)\d{3}\.\d{3}\.\d{3}-\d{2}(?!\d)")
CONTACT = ("Veratus", "São Paulo/SP", "veratus.ltda@gmail.com", "(11) 95832-3612")
PAGES_WITH_DOCUMENT = ("/", "/index.html", "/condicoes-de-compra.html")
SKIPPED_DIRS = {".git", ".venv", "node_modules", "__pycache__", "runtime"}


def _generated_document() -> str:
    # Built at run time so no CPF-shaped literal exists in the repository.
    digits = "".join(str((7 * index + 3) % 10) for index in range(11))
    return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"


@pytest.fixture
def client():
    app.config.update(TESTING=True)
    return app.test_client()


def test_document_line_appears_when_the_variable_is_set(client, monkeypatch) -> None:
    document = _generated_document()
    monkeypatch.setenv("VERATUS_SELLER_DOCUMENT", document)

    for path in PAGES_WITH_DOCUMENT:
        html = client.get(path).get_data(as_text=True)
        assert f"CPF: {document}" in html, path
        assert all(item in html for item in CONTACT), path
    assert document not in client.get("/privacy.html").get_data(as_text=True)


def test_document_line_is_absent_without_the_variable(client, monkeypatch) -> None:
    monkeypatch.delenv("VERATUS_SELLER_DOCUMENT", raising=False)

    for path in (*PAGES_WITH_DOCUMENT, "/privacy.html"):
        response = client.get(path)
        html = response.get_data(as_text=True)
        assert response.status_code == 200, path
        assert "CPF" not in html and "seller-document" not in html, path
        assert all(item in html for item in CONTACT), path


def test_unexpected_document_format_is_not_rendered(client, monkeypatch) -> None:
    monkeypatch.setenv("VERATUS_SELLER_DOCUMENT", "CPF: pendente")

    assert "CPF" not in client.get("/").get_data(as_text=True)


def _repository_files() -> list[Path]:
    try:
        listed = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        ).stdout.decode("utf-8")
        return [ROOT / name for name in listed.split("\0") if name]
    except (OSError, subprocess.CalledProcessError):
        return [
            path
            for path in ROOT.rglob("*")
            if path.is_file() and not SKIPPED_DIRS & set(path.parts)
        ]


def test_no_cpf_shaped_number_in_versioned_files() -> None:
    offenders = []
    for path in _repository_files():
        if not path.is_file():
            continue
        data = path.read_bytes()
        if b"\0" in data:
            continue  # binary asset
        if CPF_SHAPE.search(data.decode("utf-8", errors="ignore")):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []
