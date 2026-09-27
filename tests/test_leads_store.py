"""Leads saem do CSV em /tmp e vão para o banco, sem perder consentimento válido."""

from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

import pytest

from integrations import webhook
from veratus_agents.leads import LeadStore

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "import_leads_csv", ROOT / "scripts" / "import_leads_csv.py"
)
importer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(importer)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("VERATUS_AGENT_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    webhook.rate_store.clear()
    webhook.app.config.update(TESTING=True)
    return webhook.app.test_client()


def test_public_form_stores_the_lead_in_the_database(client, tmp_path) -> None:
    response = client.post(
        "/webhook",
        json={
            "email": "Cliente@Example.com",
            "utm_source": "instagram",
            "consent": "true",
            "consent_source": "formulário",
        },
    )
    store = LeadStore(tmp_path / "agent-operations.sqlite3")

    assert response.status_code == 200
    assert store.count() == 1
    # Consent is recorded by an operator, never by the public form.
    assert store.authorized() == []
    assert not list(tmp_path.glob("*.csv"))
    assert not hasattr(webhook, "LEADS_CSV")


def test_invalid_lead_is_rejected_without_storage(client, tmp_path) -> None:
    response = client.post("/webhook", json={"email": "sem-arroba"})

    assert response.status_code == 400
    assert LeadStore(tmp_path / "agent-operations.sqlite3").count() == 0


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fields = ["timestamp", "email", "whatsapp", "source", "campaign"]
    fields += ["consent", "consent_source"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_import_keeps_every_consented_row_and_is_idempotent(tmp_path) -> None:
    legacy = tmp_path / "veratus-leads.csv"
    _write_csv(
        legacy,
        [
            {
                "timestamp": "2026-09-20T10:00:00+00:00",
                "email": "a@example.com",
                "campaign": '{"utm_source": "instagram"}',
                "consent": "sim",
                "consent_source": "formulário de interesse revisado",
            },
            {"timestamp": "2026-09-21T10:00:00+00:00", "email": "b@example.com"},
            {
                "timestamp": "2026-09-22T10:00:00+00:00",
                "email": "c@example.com",
                "consent": "true",
            },
        ],
    )
    store = LeadStore(tmp_path / "ops.sqlite3")

    dry_run = importer.import_csv(legacy, None)
    assert dry_run["valid_consent"] == 1 and store.count() == 0

    first = importer.import_csv(legacy, store)
    again = importer.import_csv(legacy, store)

    assert first == {
        "rows": 3,
        "valid_consent": 1,
        "imported": 1,
        "already_imported": 0,
        "skipped": 2,
    }
    assert again["imported"] == 0 and again["already_imported"] == 1
    assert [row["email"] for row in store.authorized()] == ["a@example.com"]


def test_blueprint_no_longer_points_leads_to_tmp() -> None:
    for name in ("render.yaml", ".env.example"):
        assert "LEADS_CSV_PATH" not in (ROOT / name).read_text(encoding="utf-8")
