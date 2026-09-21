import csv
import datetime
import hashlib
import hmac
import ipaddress
import json
import os
import re
import time
import uuid
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from threading import Lock

from flask import Flask, jsonify, redirect, request, send_from_directory
from pydantic import ValidationError
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix

from veratus_agents.config import AgentSettings

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
LANDING_DIR = Path(BASE_DIR) / "landing"
app = Flask(__name__, static_folder=str(LANDING_DIR), static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 8192

LEADS_CSV = os.getenv("LEADS_CSV_PATH") or os.path.join(BASE_DIR, "leads.csv")
PORT = int(os.getenv("PORT", "5000"))

FRONTEND_ORIGINS = [
    origin.strip()
    for origin in os.getenv("FRONTEND_ORIGINS", "").split(",")
    if origin.strip()
]
DEFAULT_ALLOWED_ORIGINS = {
    "http://localhost:5000",
    "http://127.0.0.1:5000",
    "http://localhost",
    "http://127.0.0.1",
    "https://veratus.onrender.com",
    "https://www.veratus.onrender.com",
}
RATE_LIMIT_REQUESTS = max(1, int(os.getenv("RATE_LIMIT_REQUESTS", "10")))
RATE_LIMIT_WINDOW_SECONDS = max(1, int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60")))
RATE_LIMIT_MAX_BUCKETS = max(100, int(os.getenv("RATE_LIMIT_MAX_BUCKETS", "10000")))
TRUST_PROXY_HOPS = max(0, min(2, int(os.getenv("TRUST_PROXY_HOPS", "0"))))
if TRUST_PROXY_HOPS:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=TRUST_PROXY_HOPS, x_proto=1, x_host=1)

rate_store = defaultdict(list)
rate_lock = Lock()
lead_file_lock = Lock()


def _allowed_origin():
    origin = request.headers.get("Origin")
    if not origin:
        return None
    allowed = set(FRONTEND_ORIGINS) | DEFAULT_ALLOWED_ORIGINS
    return origin if origin in allowed else None


@app.after_request
def add_security_headers(response):
    origin = _allowed_origin()
    if origin:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


def ensure_csv():
    directory = os.path.dirname(LEADS_CSV) or "."
    os.makedirs(directory, exist_ok=True)
    if not os.path.exists(LEADS_CSV):
        with open(LEADS_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "timestamp",
                    "email",
                    "whatsapp",
                    "source",
                    "campaign",
                    "consent",
                    "consent_source",
                ]
            )


def _client_ip():
    return request.remote_addr or "unknown"


def _mercado_livre_notification_source_allowed() -> bool:
    configured = os.getenv("MERCADO_LIVRE_NOTIFICATION_IP_ALLOWLIST", "").strip()
    if not configured:
        return True
    try:
        source = ipaddress.ip_address(_client_ip())
    except ValueError:
        return False
    for item in configured.split(","):
        value = item.strip()
        if not value:
            continue
        try:
            network = ipaddress.ip_network(value, strict=False)
        except ValueError:
            app.logger.error("Invalid Mercado Livre notification network configured")
            return False
        if source in network:
            return True
    return False


def _rate_limited(ip_address):
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    with rate_lock:
        if len(rate_store) >= RATE_LIMIT_MAX_BUCKETS and ip_address not in rate_store:
            stale = [
                key
                for key, values in rate_store.items()
                if not values or now - values[-1] >= RATE_LIMIT_WINDOW_SECONDS
            ]
            for key in stale:
                rate_store.pop(key, None)
            if len(rate_store) >= RATE_LIMIT_MAX_BUCKETS:
                return True
        window = [
            ts for ts in rate_store[ip_address] if now - ts < RATE_LIMIT_WINDOW_SECONDS
        ]
        window.append(now)
        rate_store[ip_address] = window
        return len(window) > RATE_LIMIT_REQUESTS


def _normalize_lead(data):
    email = (data.get("email") or "").strip().lower()
    whatsapp = (data.get("whatsapp") or "").strip()
    source = (data.get("source") or "landing").strip() or "landing"

    if not email and not whatsapp:
        raise ValueError("Informe seu e-mail ou WhatsApp para continuar.")
    if email and "@" not in email:
        raise ValueError("O e-mail informado parece inválido.")

    return {
        "email": email,
        "whatsapp": whatsapp,
        "source": source,
    }


def _safe_csv_cell(value: str) -> str:
    value = value[:500]
    return "'" + value if value.startswith(("=", "+", "-", "@")) else value


@app.route("/health", methods=["GET", "HEAD", "OPTIONS"])
def health():
    if request.method == "OPTIONS":
        return "", 204
    return jsonify({"status": "ok"}), 200


@app.route("/", methods=["GET"])
def landing():
    return send_from_directory(LANDING_DIR, "index.html")


@app.route("/api/catalog", methods=["GET", "OPTIONS"])
def public_product_catalog():
    """Serve the customer-safe projection of the Product Master."""
    if request.method == "OPTIONS":
        return "", 204
    from veratus_agents.catalog import public_catalog

    return jsonify(public_catalog()), 200


@app.route("/webhook", methods=["POST", "OPTIONS"])
def webhook():
    if request.method == "OPTIONS":
        return "", 204

    if _rate_limited(_client_ip()):
        return jsonify({"status": "error", "message": "rate_limited"}), 429

    data = request.get_json(silent=True)
    if data is None:
        data = request.form.to_dict() or {}

    try:
        lead = _normalize_lead(data)
    except (TypeError, ValueError) as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400

    ensure_csv()
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    campaign = {
        key: str(data[key])[:200]
        for key in ("utm_source", "utm_medium", "utm_campaign", "utm_content")
        if data.get(key)
    }
    with lead_file_lock, open(LEADS_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                timestamp,
                _safe_csv_cell(lead["email"]),
                _safe_csv_cell(lead["whatsapp"]),
                _safe_csv_cell(lead["source"]),
                json.dumps(campaign, ensure_ascii=False),
                "",
                "",
            ]
        )

    return jsonify({"status": "ok", "message": "lead_queued_for_review"}), 200


def _agent_authorized(header_name, variable_name):
    expected = (os.getenv(variable_name) or "").strip()
    provided = (request.headers.get(header_name) or "").strip()
    return bool(expected and provided and hmac.compare_digest(provided, expected))


@lru_cache(maxsize=4)
def _agent_store_cached(operations_db: str, database_url: str | None):
    from veratus_agents.storage import make_run_repository

    return make_run_repository(operations_db, database_url)


def _agent_store():
    settings = AgentSettings.from_env()
    return _agent_store_cached(str(settings.operations_db), settings.database_url)


@lru_cache(maxsize=4)
def _metrics_store_cached(operations_db: str, database_url: str | None):
    from veratus_agents.metrics import make_metrics_repository

    return make_metrics_repository(operations_db, database_url)


def _metrics_store():
    settings = AgentSettings.from_env()
    return _metrics_store_cached(str(settings.operations_db), settings.database_url)


@lru_cache(maxsize=4)
def _product_store_cached(operations_db: str, database_url: str | None):
    from veratus_agents.product_master import make_product_repository

    return make_product_repository(operations_db, database_url)


def _product_store():
    settings = AgentSettings.from_env()
    return _product_store_cached(str(settings.operations_db), settings.database_url)


@lru_cache(maxsize=4)
def _marketplace_store_cached(path: str):
    from veratus_agents.marketplace_ops import MarketplaceStore

    return MarketplaceStore(path)


def _marketplace_store():
    settings = AgentSettings.from_env()
    return _marketplace_store_cached(str(settings.runtime_dir / "marketplace.sqlite3"))


@lru_cache(maxsize=4)
def _mercado_oauth_store_cached(
    database_url: str, runtime_dir: str, encryption_key: str
):
    from veratus_agents.mercado_livre_oauth import MercadoLivreOAuthStore

    return MercadoLivreOAuthStore(
        encryption_key=encryption_key,
        database_url=database_url or None,
        sqlite_path=os.path.join(runtime_dir, "mercado-livre-oauth.sqlite3"),
    )


def _mercado_oauth_store():
    settings = AgentSettings.from_env()
    return _mercado_oauth_store_cached(
        os.getenv("DATABASE_URL", "").strip(),
        str(settings.runtime_dir),
        os.getenv("VERATUS_TOKEN_ENCRYPTION_KEY", "").strip(),
    )


def _agent_json():
    if not request.is_json:
        return None, ("content_type_must_be_json", 415)
    if request.content_length is not None and request.content_length > 8192:
        return None, ("payload_too_large", 413)
    request.max_content_length = 8192
    try:
        raw = request.get_data(cache=True)
        if len(raw) > 8192:
            return None, ("payload_too_large", 413)
        data = request.get_json(force=False)
    except (BadRequest, RequestEntityTooLarge) as exc:
        if isinstance(exc, RequestEntityTooLarge):
            return None, ("payload_too_large", 413)
        return None, ("invalid_json", 400)
    if not isinstance(data, dict):
        return None, ("json_object_required", 400)
    return data, None


@app.route("/agent/health", methods=["GET"])
def agent_health():
    try:
        import agents  # noqa: F401

        sdk_installed = True
    except ImportError:
        sdk_installed = False
    ready = all(
        (
            sdk_installed,
            bool((os.getenv("OPENAI_API_KEY") or "").strip()),
            bool((os.getenv("VERATUS_AGENT_SHARED_SECRET") or "").strip()),
            bool((os.getenv("VERATUS_ADMIN_TOKEN") or "").strip()),
            bool((os.getenv("VERATUS_SESSION_SALT") or "").strip()),
            bool((os.getenv("DATABASE_URL") or "").strip()),
        )
    )
    return jsonify(
        {
            "status": "configured" if ready else "not_configured",
            "external_sending_enabled": False,
        }
    ), 200


@app.route("/agent/sales/draft", methods=["POST"])
def agent_sales_draft():
    if _rate_limited("agent:" + _client_ip()):
        return jsonify({"status": "error", "message": "rate_limited"}), 429
    if not _agent_authorized("X-Veratus-Agent-Key", "VERATUS_AGENT_SHARED_SECRET"):
        return jsonify({"status": "error", "message": "unauthorized"}), 401
    data, error = _agent_json()
    if error:
        return jsonify({"status": "error", "message": error[0]}), error[1]
    fields = ("customer_ref", "message", "source", "product_hint", "event_id")
    if any(key in data and not isinstance(data[key], str) for key in fields):
        return jsonify({"status": "error", "message": "invalid_field_type"}), 400
    customer_ref = (data.get("customer_ref") or "").strip()
    message = (data.get("message") or "").strip()
    source = (data.get("source") or "api").strip() or "api"
    product_hint = (data.get("product_hint") or "").strip() or None
    event_id = (data.get("event_id") or "").strip() or None
    if not customer_ref or not message:
        return jsonify(
            {"status": "error", "message": "customer_ref_and_message_required"}
        ), 400
    if (
        len(customer_ref) > 256
        or len(message) > 4000
        or len(source) > 100
        or (product_hint and len(product_hint) > 200)
        or (event_id and len(event_id) > 200)
    ):
        return jsonify({"status": "error", "message": "payload_too_large"}), 413
    if not re.fullmatch(r"[a-z0-9:_-]+", source, flags=re.IGNORECASE):
        return jsonify({"status": "error", "message": "invalid_source"}), 400
    request_id = uuid.uuid4().hex[:12]
    started = time.monotonic()
    try:
        from veratus_agents import AgentConfigurationError, run_sales_workflow

        result = run_sales_workflow(
            customer_ref=customer_ref,
            message=message,
            source=source,
            product_hint=product_hint,
            event_id=event_id,
            request_id=request_id,
        )
    except AgentConfigurationError:
        app.logger.warning("agent_config_missing request_id=%s", request_id)
        return jsonify(
            {
                "status": "error",
                "message": "agent_not_configured",
                "request_id": request_id,
            }
        ), 503
    except Exception as exc:  # noqa: BLE001 - API boundary sanitizes all agent failures
        app.logger.error(
            "agent_failed request_id=%s error_type=%s", request_id, type(exc).__name__
        )
        return jsonify(
            {
                "status": "error",
                "message": "agent_workflow_failed",
                "request_id": request_id,
            }
        ), 502
    app.logger.info(
        "agent_completed request_id=%s run_id=%s status=%s duration_ms=%d",
        request_id,
        result["run_id"],
        result["status"],
        int((time.monotonic() - started) * 1000),
    )
    status_code = 202 if result.get("status") == "running" else 200
    return jsonify(result), status_code


@app.route("/agent/live/draft", methods=["POST"])
def agent_live_draft():
    if _rate_limited("agent-live:" + _client_ip()):
        return jsonify({"status": "error", "message": "rate_limited"}), 429
    if not _agent_authorized("X-Veratus-Agent-Key", "VERATUS_AGENT_SHARED_SECRET"):
        return jsonify({"status": "error", "message": "unauthorized"}), 401
    data, error = _agent_json()
    if error:
        return jsonify({"status": "error", "message": error[0]}), error[1]
    allowed = {"live_session_id", "viewer_ref", "message", "product_hint", "event_id"}
    if set(data) - allowed or any(
        not isinstance(value, str) for value in data.values()
    ):
        return jsonify({"status": "error", "message": "invalid_live_payload"}), 400
    live_id = data.get("live_session_id", "").strip()
    viewer_ref = data.get("viewer_ref", "").strip()
    message = data.get("message", "").strip()
    if not live_id or not viewer_ref or not message:
        return jsonify({"status": "error", "message": "live_fields_required"}), 400
    if any(
        len(value) > limit
        for value, limit in ((live_id, 120), (viewer_ref, 256), (message, 2000))
    ):
        return jsonify({"status": "error", "message": "payload_too_large"}), 413
    if not re.fullmatch(r"[a-z0-9_-]+", live_id, flags=re.IGNORECASE):
        return jsonify({"status": "error", "message": "invalid_live_session_id"}), 400
    for optional_field, limit in (("product_hint", 200), ("event_id", 200)):
        if len((data.get(optional_field) or "").strip()) > limit:
            return jsonify({"status": "error", "message": "payload_too_large"}), 413
    request_id = uuid.uuid4().hex[:12]
    try:
        from veratus_agents import run_sales_workflow

        result = run_sales_workflow(
            customer_ref=viewer_ref,
            message=f"Pergunta recebida durante a live {live_id}: {message}",
            source=f"live:{live_id}",
            product_hint=(data.get("product_hint") or "").strip() or None,
            event_id=(data.get("event_id") or "").strip() or None,
            request_id=request_id,
        )
    except Exception as exc:  # noqa: BLE001 - API boundary sanitizes failures
        app.logger.error(
            "live_agent_failed request_id=%s type=%s", request_id, type(exc).__name__
        )
        return jsonify(
            {
                "status": "error",
                "message": "agent_workflow_failed",
                "request_id": request_id,
            }
        ), 502
    return jsonify(result), 202 if result.get("status") == "running" else 200


_RUN_STATUSES = {
    "running",
    "pending_review",
    "needs_revision",
    "approved",
    "rejected",
    "failed",
}


@app.route("/agent/runs", methods=["GET"])
def agent_list_runs():
    if _rate_limited("agent-admin:" + _client_ip()):
        return jsonify({"status": "error", "message": "rate_limited"}), 429
    if not _agent_authorized("X-Veratus-Admin-Token", "VERATUS_ADMIN_TOKEN"):
        return jsonify({"status": "error", "message": "unauthorized"}), 401
    status = request.args.get("status") or None
    if status and status not in _RUN_STATUSES:
        return jsonify({"status": "error", "message": "invalid_status"}), 400
    try:
        limit = int(request.args.get("limit", "50"))
    except ValueError:
        return jsonify({"status": "error", "message": "invalid_limit"}), 400
    return jsonify({"runs": _agent_store().list_runs(status=status, limit=limit)}), 200


@app.route("/agent/runs/<run_id>", methods=["GET"])
def agent_get_run(run_id):
    if _rate_limited("agent-admin:" + _client_ip()):
        return jsonify({"status": "error", "message": "rate_limited"}), 429
    if not _agent_authorized("X-Veratus-Admin-Token", "VERATUS_ADMIN_TOKEN"):
        return jsonify({"status": "error", "message": "unauthorized"}), 401
    run = _agent_store().get_run(run_id)
    if run is None:
        return jsonify({"status": "error", "message": "run_not_found"}), 404
    return jsonify(run), 200


@app.route("/agent/runs/<run_id>/decision", methods=["POST"])
def agent_decide_run(run_id):
    if _rate_limited("agent-admin:" + _client_ip()):
        return jsonify({"status": "error", "message": "rate_limited"}), 429
    if not _agent_authorized("X-Veratus-Admin-Token", "VERATUS_ADMIN_TOKEN"):
        return jsonify({"status": "error", "message": "unauthorized"}), 401
    data, error = _agent_json()
    if error:
        return jsonify({"status": "error", "message": error[0]}), error[1]
    raw_decision = data.get("decision")
    decision = (
        {"approve": "approved", "reject": "rejected"}.get(raw_decision)
        if isinstance(raw_decision, str)
        else None
    )
    note = data.get("note", "")
    actor = data.get("actor", "")
    approved_reply = data.get("approved_reply")
    if (
        decision is None
        or not isinstance(note, str)
        or not isinstance(actor, str)
        or not actor.strip()
        or len(actor) > 100
        or len(note) > 2000
        or (approved_reply is not None and not isinstance(approved_reply, str))
        or (isinstance(approved_reply, str) and len(approved_reply) > 1800)
        or (decision == "approved" and not (approved_reply or "").strip())
    ):
        return jsonify({"status": "error", "message": "invalid_decision"}), 400
    store = _agent_store()
    if store.get_run(run_id) is None:
        return jsonify({"status": "error", "message": "run_not_found"}), 404
    if not store.decide(
        run_id,
        decision=decision,
        actor=actor,
        note=note,
        approved_reply=approved_reply,
    ):
        return jsonify({"status": "error", "message": "run_not_pending_review"}), 409
    return jsonify(
        {
            "status": "ok",
            "run_id": run_id,
            "decision": decision,
            "external_message_sent": False,
        }
    ), 200


@app.route("/agent/metrics/records", methods=["POST"])
def agent_add_metric_record():
    if _rate_limited("agent-admin:" + _client_ip()):
        return jsonify({"status": "error", "message": "rate_limited"}), 429
    if not _agent_authorized("X-Veratus-Admin-Token", "VERATUS_ADMIN_TOKEN"):
        return jsonify({"status": "error", "message": "unauthorized"}), 401
    data, error = _agent_json()
    if error:
        return jsonify({"status": "error", "message": error[0]}), error[1]
    from veratus_agents.schemas import PerformanceRecord

    try:
        record = PerformanceRecord.model_validate(data)
    except ValidationError:
        return jsonify(
            {"status": "error", "message": "invalid_performance_record"}
        ), 400
    created = _metrics_store().add(record)
    return jsonify(
        {"status": "created" if created else "duplicate", "event_id": record.event_id}
    ), 201 if created else 200


@app.route("/agent/metrics/summary", methods=["GET"])
def agent_metrics_summary():
    if _rate_limited("agent-admin:" + _client_ip()):
        return jsonify({"status": "error", "message": "rate_limited"}), 429
    if not _agent_authorized("X-Veratus-Admin-Token", "VERATUS_ADMIN_TOKEN"):
        return jsonify({"status": "error", "message": "unauthorized"}), 401
    from veratus_agents.metrics import summarize_performance

    def parse_date(name):
        value = request.args.get(name)
        return datetime.date.fromisoformat(value) if value else None

    try:
        date_from, date_to = parse_date("from"), parse_date("to")
    except ValueError:
        return jsonify({"status": "error", "message": "invalid_date"}), 400
    records = _metrics_store().list_records(
        date_from=date_from,
        date_to=date_to,
        channel=request.args.get("channel") or None,
        campaign=request.args.get("campaign") or None,
    )
    return jsonify(summarize_performance(records)), 200


def _admin_required():
    if _rate_limited("os-admin:" + _client_ip()):
        return jsonify({"status": "error", "message": "rate_limited"}), 429
    if not _agent_authorized("X-Veratus-Admin-Token", "VERATUS_ADMIN_TOKEN"):
        return jsonify({"status": "error", "message": "unauthorized"}), 401
    return None


@app.route("/integrations/mercado-livre/oauth/start", methods=["GET"])
def mercado_livre_oauth_start():
    denied = _admin_required()
    if denied:
        return denied
    from veratus_agents.marketplace_clients import (
        MarketplaceApiError,
        MercadoLivreOAuthTokenManager,
    )
    from veratus_agents.mercado_livre_oauth import OAuthConfigurationError

    try:
        store = _mercado_oauth_store()
        manager = MercadoLivreOAuthTokenManager.from_env(store)
        ttl = int(os.getenv("MERCADO_LIVRE_OAUTH_STATE_TTL_SECONDS", "600"))
        authorization_url = manager.authorization_url(store.create_state(ttl))
    except (OAuthConfigurationError, MarketplaceApiError, ValueError):
        return jsonify(
            {"status": "blocked", "message": "oauth_configuration_incomplete"}
        ), 503
    if (
        request.args.get("format") == "json"
        or request.accept_mimetypes.best == "application/json"
    ):
        response = jsonify({"status": "ready", "authorization_url": authorization_url})
    else:
        response = redirect(authorization_url, code=302)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/integrations/mercado-livre/oauth/callback", methods=["GET"])
def mercado_livre_oauth_callback():
    from veratus_agents.marketplace_clients import (
        MarketplaceApiError,
        MercadoLivreOAuthTokenManager,
    )
    from veratus_agents.mercado_livre_oauth import (
        OAuthConfigurationError,
        OAuthStateError,
        TokenStorageError,
    )

    state = request.args.get("state", "")
    try:
        store = _mercado_oauth_store()
        store.consume_state(state)
    except OAuthStateError:
        return jsonify({"status": "error", "message": "invalid_oauth_state"}), 400
    except OAuthConfigurationError:
        return jsonify(
            {"status": "blocked", "message": "oauth_storage_not_configured"}
        ), 503

    if request.args.get("error"):
        return jsonify(
            {"status": "error", "message": "authorization_not_completed"}
        ), 400

    code = request.args.get("code", "")
    if not re.fullmatch(r"[A-Za-z0-9._~-]{8,2048}", code):
        return jsonify(
            {"status": "error", "message": "invalid_authorization_code"}
        ), 400
    try:
        manager = MercadoLivreOAuthTokenManager.from_env(store)
        stored = manager.exchange_code(code)
        code_digest = hashlib.sha256(code.encode("utf-8")).hexdigest()[:24]
        execution = _operational_runtime().execute(
            "Gerente, valide a conexão do Mercado Livre e atualize a prontidão dos produtos.",
            idempotency_key=f"mercado-livre-oauth:{code_digest}",
        )
    except (MarketplaceApiError, OAuthConfigurationError, TokenStorageError):
        return jsonify(
            {"status": "error", "message": "oauth_token_exchange_failed"}
        ), 502

    readiness_task = next(
        (
            task
            for task in reversed(execution.get("tasks", []))
            if task.get("action") == "CONSOLIDATE_MERCADO_LIVRE_READINESS"
        ),
        None,
    )
    connected = bool(
        readiness_task and readiness_task.get("result", {}).get("read_only_connection")
    )
    response = jsonify(
        {
            "status": "connected_read_only"
            if connected
            else "authorized_check_pending",
            "token": stored.public_status(),
            "connection_health": "HEALTHY" if connected else "NOT_READY",
            "publish_enabled": False,
            "external_writes": False,
        }
    )
    response.headers["Cache-Control"] = "no-store"
    return response, 200


@app.route("/integrations/mercado-livre/notifications", methods=["GET", "POST"])
def mercado_livre_notifications():
    from veratus_agents.mercado_livre_oauth import (
        OAuthConfigurationError,
        TokenStorageError,
        validate_notification_payload,
    )

    if request.method == "GET":
        return jsonify(
            {
                "status": "ready",
                "handler": "mercado-livre-notifications",
                "external_writes": False,
            }
        ), 200
    if not _mercado_livre_notification_source_allowed():
        return jsonify(
            {"status": "error", "message": "notification_source_denied"}
        ), 403
    if not request.is_json:
        return jsonify({"status": "error", "message": "content_type_must_be_json"}), 415
    try:
        store = _mercado_oauth_store()
        tokens = store.load_tokens(required=False)
        payload = validate_notification_payload(
            request.get_json(silent=True),
            expected_application_id=os.getenv("MERCADO_LIVRE_CLIENT_ID", "").strip(),
            expected_user_id=tokens.user_id if tokens else None,
        )
        event_id, inserted = store.enqueue_notification(payload)
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc).lower()}), 400
    except (OAuthConfigurationError, TokenStorageError):
        return jsonify(
            {"status": "blocked", "message": "notification_storage_not_configured"}
        ), 503
    return jsonify(
        {
            "status": "accepted" if inserted else "duplicate",
            "event_id": event_id,
            "processing": "PENDING_SYNC_MONITOR" if inserted else "ALREADY_QUEUED",
        }
    ), 200


@app.route("/integrations/mercado-livre/status", methods=["GET"])
def mercado_livre_integration_status():
    denied = _admin_required()
    if denied:
        return denied
    from veratus_agents.mercado_livre_oauth import (
        OAuthConfigurationError,
        TokenStorageError,
    )

    try:
        store = _mercado_oauth_store()
        tokens = store.load_tokens(required=False)
        return jsonify(
            {
                "status": "authorized" if tokens else "not_authorized",
                "token": tokens.public_status() if tokens else {"stored": False},
                "notifications_queued": store.notification_count(),
                "publish_enabled": False,
                "external_writes": False,
            }
        ), 200
    except (OAuthConfigurationError, TokenStorageError):
        return jsonify(
            {"status": "blocked", "message": "oauth_storage_not_configured"}
        ), 503


@app.route("/os/pricing/calculate", methods=["POST"])
def os_calculate_pricing():
    denied = _admin_required()
    if denied:
        return denied
    data, error = _agent_json()
    if error:
        return jsonify({"status": "error", "message": error[0]}), error[1]
    from veratus_agents.product_master import PricingInput, calculate_pricing

    try:
        result = calculate_pricing(PricingInput.model_validate(data))
    except ValidationError:
        return jsonify({"status": "error", "message": "invalid_pricing_input"}), 400
    return jsonify({"status": "ok", "pricing": result}), 200


@app.route("/os/products", methods=["GET", "POST"])
def os_products():
    denied = _admin_required()
    if denied:
        return denied
    from veratus_agents.product_master import ProductCreate, ProductStatus

    if request.method == "GET":
        raw_status = request.args.get("status")
        try:
            status = ProductStatus(raw_status) if raw_status else None
            limit = int(request.args.get("limit", "100"))
        except (ValueError, TypeError):
            return jsonify({"status": "error", "message": "invalid_filter"}), 400
        return jsonify(
            {"products": _product_store().list_products(status=status, limit=limit)}
        ), 200

    data, error = _agent_json()
    if error:
        return jsonify({"status": "error", "message": error[0]}), error[1]
    if set(data) != {"actor", "event_id", "product"}:
        return jsonify({"status": "error", "message": "invalid_product_request"}), 400
    actor, event_id = data.get("actor"), data.get("event_id")
    if not isinstance(actor, str) or not isinstance(event_id, str):
        return jsonify({"status": "error", "message": "invalid_product_request"}), 400
    if not re.fullmatch(r"[A-Za-z0-9._:-]{8,200}", event_id):
        return jsonify({"status": "error", "message": "invalid_event_id"}), 400
    try:
        product = ProductCreate.model_validate(data["product"])
        created = _product_store().create(product, actor=actor, event_id=event_id)
    except (ValidationError, ValueError):
        return jsonify({"status": "error", "message": "invalid_product"}), 400
    return jsonify({"status": "created", "product": created}), 201


@app.route("/os/products/<sku>", methods=["GET", "PATCH"])
def os_product(sku):
    denied = _admin_required()
    if denied:
        return denied
    store = _product_store()
    if request.method == "GET":
        product = store.get(sku)
        if product is None:
            return jsonify({"status": "error", "message": "product_not_found"}), 404
        return jsonify({"product": product}), 200

    data, error = _agent_json()
    if error:
        return jsonify({"status": "error", "message": error[0]}), error[1]
    if set(data) != {"actor", "event_id", "reason", "changes"}:
        return jsonify({"status": "error", "message": "invalid_update_request"}), 400
    if any(
        not isinstance(data[field], str) for field in ("actor", "event_id", "reason")
    ) or not isinstance(data["changes"], dict):
        return jsonify({"status": "error", "message": "invalid_update_request"}), 400
    from veratus_agents.product_master import ProductUpdate

    try:
        update = ProductUpdate.model_validate(data["changes"])
        product = store.update(
            sku,
            update,
            actor=data["actor"],
            event_id=data["event_id"],
            reason=data["reason"],
        )
    except (ValidationError, ValueError):
        return jsonify({"status": "error", "message": "invalid_product_update"}), 400
    if product is None:
        return jsonify({"status": "error", "message": "product_not_found"}), 404
    return jsonify({"status": "updated", "product": product}), 200


def _product_transition(sku: str, *, override: bool):
    denied = _admin_required()
    if denied:
        return denied
    data, error = _agent_json()
    if error:
        return jsonify({"status": "error", "message": error[0]}), error[1]
    if set(data) != {"actor", "event_id", "reason", "target"}:
        return jsonify(
            {"status": "error", "message": "invalid_transition_request"}
        ), 400
    if any(
        not isinstance(data[field], str)
        for field in ("actor", "event_id", "reason", "target")
    ):
        return jsonify(
            {"status": "error", "message": "invalid_transition_request"}
        ), 400
    from veratus_agents.product_master import ProductStatus

    try:
        target = ProductStatus(data["target"])
        product, issues = _product_store().transition(
            sku,
            target=target,
            actor=data["actor"],
            event_id=data["event_id"],
            reason=data["reason"],
            override=override,
        )
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "invalid_transition"}), 400
    if product is None:
        return jsonify({"status": "error", "message": "product_not_found"}), 404
    if issues:
        return jsonify({"status": "blocked", "issues": issues, "product": product}), 409
    return jsonify(
        {
            "status": "overridden" if override else "transitioned",
            "product": product,
        }
    ), 200


@app.route("/os/products/<sku>/transition", methods=["POST"])
def os_product_transition(sku):
    return _product_transition(sku, override=False)


@app.route("/os/products/<sku>/override", methods=["POST"])
def os_product_override(sku):
    return _product_transition(sku, override=True)


@app.route("/os/products/<sku>/events", methods=["GET"])
def os_product_events(sku):
    denied = _admin_required()
    if denied:
        return denied
    if _product_store().get(sku) is None:
        return jsonify({"status": "error", "message": "product_not_found"}), 404
    return jsonify({"events": _product_store().events(sku)}), 200


@app.route("/os/distribution/drafts/<sku>", methods=["POST"])
def os_distribution_draft(sku):
    denied = _admin_required()
    if denied:
        return denied
    data, error = _agent_json()
    if error:
        return jsonify({"status": "error", "message": error[0]}), error[1]
    channel_from_path = request.view_args.get("channel") if request.view_args else None
    if set(data) not in ({"channel"}, set()) or (
        "channel" in data and not isinstance(data["channel"], str)
    ):
        return jsonify(
            {"status": "error", "message": "invalid_distribution_request"}
        ), 400
    if channel_from_path:
        data = {"channel": channel_from_path}

    product = _product_store().get(sku)
    if product is None:
        return jsonify({"status": "error", "message": "product_not_found"}), 404

    from veratus_agents.marketplace_adapters import (
        DistributionDraftError,
        Marketplace,
        build_distribution_draft,
    )
    from veratus_agents.marketplace_ops import (
        MarketplaceValidationError,
        prepare_listing,
    )

    try:
        channel = Marketplace(data["channel"])
        draft = build_distribution_draft(product, channel)
        listing = prepare_listing(
            _marketplace_store(), product, channel, draft["payload"]
        )
    except (DistributionDraftError, MarketplaceValidationError, ValueError):
        return jsonify(
            {"status": "error", "message": "distribution_draft_blocked"}
        ), 409
    return jsonify({"status": "draft", "draft": draft, "listing": listing}), 200


@app.route("/os/channels", methods=["GET"])
def os_channels():
    denied = _admin_required()
    if denied:
        return denied
    store = _marketplace_store()
    result = []
    for channel in store.channels():
        account = store.account(channel["id"])
        channel["account"] = {
            "channel": account.channel,
            "account_id": account.account_id,
            "store_id": account.store_id,
            "region": account.region,
            "currency": account.currency,
            "enabled": account.enabled,
            "credential_reference": account.credential_reference,
            "connected": account.connected,
            "last_health_check": account.last_health_check,
        }
        channel["readiness"] = "READY_FOR_DRAFT" if channel["enabled"] else "NOT_READY"
        result.append(channel)
    return jsonify(
        {
            "channels": result,
            "publish_enabled": os.getenv("PUBLISH_ENABLED", "false").lower() == "true",
        }
    ), 200


@app.route("/os/channels/<channel>", methods=["GET"])
def os_channel(channel):
    denied = _admin_required()
    if denied:
        return denied
    item = _marketplace_store().channel(channel)
    if item is None:
        return jsonify({"status": "error", "message": "channel_not_found"}), 404
    return jsonify(
        {"channel": item, "account": _marketplace_store().account(channel).__dict__}
    ), 200


@app.route("/os/listings", methods=["GET"])
def os_listings():
    denied = _admin_required()
    if denied:
        return denied
    return jsonify(
        {"listings": _marketplace_store().listings(request.args.get("channel"))}
    ), 200


@app.route("/os/listings/<channel>/<sku>", methods=["GET"])
def os_listing(channel, sku):
    denied = _admin_required()
    if denied:
        return denied
    listing = _marketplace_store().listing(channel, sku)
    if listing is None:
        return jsonify({"status": "error", "message": "listing_not_found"}), 404
    return jsonify({"listing": listing}), 200


@app.route("/os/listings/<channel>/<sku>/prepare", methods=["POST"])
def os_listing_prepare(channel, sku):
    return os_distribution_draft(sku)


@app.route("/os/listings/<channel>/<sku>/publish", methods=["POST"])
def os_listing_publish(channel, sku):
    denied = _admin_required()
    if denied:
        return denied
    product = _product_store().get(sku)
    if product is None:
        return jsonify({"status": "error", "message": "product_not_found"}), 404
    store = _marketplace_store()
    approvals = {item["request_id"]: item for item in store.approvals()}
    approval = approvals.get(f"publish_{channel}_{sku}")
    if approval is None:
        approval = store.request_approval(
            channel,
            sku,
            {
                "sku": sku,
                "channel": channel,
                "price": product.get("price_brl"),
                "stock": product.get("availability"),
            },
        )
        return jsonify(
            {
                "status": "approval_required",
                "approval": approval,
                "external_write": False,
            }
        ), 409

    class Approval:
        status = approval.get("status") if approval else None

    from veratus_agents.marketplace_ops import ExternalWriteGuard

    allowed, reasons = ExternalWriteGuard(store).check(
        channel,
        product,
        approval=Approval(),
        publish_enabled=os.getenv("PUBLISH_ENABLED", "false").lower() == "true",
    )
    if not allowed:
        return jsonify(
            {"status": "blocked", "reasons": reasons, "external_write": False}
        ), 409
    return jsonify(
        {
            "status": "blocked",
            "reasons": ["EXTERNAL_CLIENT_NOT_IMPLEMENTED"],
            "external_write": False,
        }
    ), 409


@app.route("/os/listings/<channel>/<sku>/pause", methods=["POST"])
def os_listing_pause(channel, sku):
    denied = _admin_required()
    if denied:
        return denied
    listing = _marketplace_store().listing(channel, sku)
    if listing is None:
        return jsonify({"status": "error", "message": "listing_not_found"}), 404
    from veratus_agents.marketplace_ops import ListingState

    return jsonify(
        {
            "listing": _marketplace_store().upsert_listing(
                sku, channel, ListingState.PAUSED
            ),
            "external_write": False,
        }
    ), 200


@app.route("/os/sync", methods=["POST"])
def os_sync():
    denied = _admin_required()
    if denied:
        return denied
    store = _marketplace_store()
    conflicts = store.sync_conflicts()
    return jsonify(
        {
            "status": "ok",
            "conflicts": conflicts,
            "checked": len(store.listings()),
            "external_write": False,
        }
    ), 200


@app.route("/os/sync/conflicts", methods=["GET"])
def os_sync_conflicts():
    denied = _admin_required()
    if denied:
        return denied
    return jsonify({"conflicts": _marketplace_store().sync_conflicts()}), 200


@app.route("/os/data-quality", methods=["GET"])
def os_data_quality():
    denied = _admin_required()
    if denied:
        return denied
    from veratus_agents.data_discovery import discovery_report

    report = discovery_report()
    missing_by_field = {}
    for item in report["missing_business_data"]:
        missing_by_field[item["field"]] = missing_by_field.get(item["field"], 0) + 1
    missing_by_channel = {}
    for item in report["missing_business_data"]:
        missing_by_channel[item["channel"]] = (
            missing_by_channel.get(item["channel"], 0) + 1
        )
    return jsonify(
        {
            "products_total": report["products_total"],
            "products_complete": report["products_complete"],
            "products_incomplete": report["products_incomplete"],
            "segments": report["segments"],
            "conflicts": report["data_conflicts"],
            "official_commercial_data": report["official_commercial_data"],
            "official_economics": report["official_economics"],
            "missing_by_field": missing_by_field,
            "missing_by_channel": missing_by_channel,
            "missing_business_data": report["missing_business_data"],
        }
    ), 200


@app.route("/os/readiness", methods=["GET"])
def os_readiness():
    denied = _admin_required()
    if denied:
        return denied
    from veratus_agents.data_discovery import discovery_report, first_publish_candidate
    from veratus_agents.marketplace_clients import connection_status, credential_matrix

    report = discovery_report()
    real_connections = connection_status()
    store = _marketplace_store()
    for check in store.connection_checks():
        real_connections[check["channel"]] = check
    channels = []
    for item in store.channels():
        account = store.account(item["id"])
        credential_present = account.credential_reference is not None
        mappings = store.category_mappings(item["id"])
        mapping_status = (
            mappings[0]["status"]
            if mappings
            else "CATEGORY_DISCOVERY_BLOCKED_BY_CREDENTIALS"
        )
        channels.append(
            {
                "channel": item["id"],
                "credentials": "PRESENT" if credential_present else "MISSING",
                "account": "PRESENT" if account.account_id else "MISSING",
                "category_mappings": mapping_status,
                "attribute_mappings": "API_CONTRACT_UNVERIFIED",
                "client": "API_CONTRACT_UNVERIFIED",
                "read_access": "NOT_TESTED",
                "draft_readiness": "PRODUCT_DATA_COMPLETE_CHANNEL_BLOCKED",
                "publish_readiness": "NOT_READY",
                "reason": "credenciais/categoria/client oficial ainda não validados",
                "real_connection": real_connections[item["id"]],
            }
        )
    publish_plans = store.publish_plans()
    latest_plan = publish_plans[-1] if publish_plans else None
    channel_modes = {
        channel: check.get("readiness", "NOT_READY")
        for channel, check in real_connections.items()
    }
    return jsonify(
        {
            "veratus_os": "READY_FOR_DRAFT",
            "marketplace_mode": os.getenv("MARKETPLACE_MODE", "LOCAL"),
            "channel_modes": channel_modes,
            "channels": channels,
            "products_total": report["products_total"],
            "products_complete": report["products_complete"],
            "segments": report["segments"],
            "missing_business_data": report["missing_business_data"],
            "first_publish_candidate": first_publish_candidate(report["products"]),
            "credential_matrix": credential_matrix(),
            "real_connection_status": real_connections,
            "ready_for_first_live_publish": latest_plan is not None,
            "first_real_publish_plan": latest_plan,
        }
    ), 200


@app.route("/os/connections/refresh", methods=["POST"])
def os_connections_refresh():
    denied = _admin_required()
    if denied:
        return denied
    execution = _operational_runtime().execute(
        "Gerente, valide a conexão do Mercado Livre e atualize a prontidão dos produtos.",
        idempotency_key=f"mercado-livre-connection-refresh:{int(time.time())}",
    )
    return jsonify({"status": "completed", "execution": _json_safe(execution)}), 200


@app.route("/os/publish-plans", methods=["GET"])
def os_publish_plans():
    denied = _admin_required()
    if denied:
        return denied
    return jsonify({"plans": _marketplace_store().publish_plans()}), 200


@app.route("/os/commands/simulate-distribution", methods=["POST"])
def os_simulate_distribution():
    denied = _admin_required()
    if denied:
        return denied
    data, error = _agent_json()
    if error:
        return jsonify({"status": "error", "message": error[0]}), error[1]
    if set(data) != {"product_count"} or not isinstance(data["product_count"], int):
        return jsonify(
            {"status": "error", "message": "invalid_simulation_request"}
        ), 400
    if not 0 < data["product_count"] <= 500:
        return jsonify({"status": "error", "message": "invalid_product_count"}), 400

    from veratus_agents.operations import simulate_prepare_distribution

    return jsonify(simulate_prepare_distribution(data["product_count"])), 200


_runtime_engine = None
_operational_runtime_instance = None


def _json_safe(value):
    return json.loads(
        json.dumps(
            value,
            default=lambda item: (
                item.value
                if hasattr(item, "value")
                else list(item)
                if isinstance(item, (set, frozenset, tuple))
                else str(item)
            ),
        )
    )


def _runtime():
    global _runtime_engine
    if _runtime_engine is None:
        _runtime_engine = _operational_runtime().engine
    return _runtime_engine


def _operational_runtime():
    global _operational_runtime_instance
    if _operational_runtime_instance is None:
        from veratus_agents.data_discovery import discover_products
        from veratus_agents.marketplace_ops import CategoryMappingStatus
        from veratus_agents.marketplace_service import MarketplaceConnectionService
        from veratus_agents.operations import OperationalRuntime

        settings = AgentSettings.from_env()
        store = _marketplace_store()
        for channel in ("mercado-livre", "shopee", "tiktok-shop", "meta"):
            for canonical_category in (
                "Relógios",
                "jewelry_accessories/necklace",
                "jewelry_accessories/bracelet",
                "jewelry_accessories/anklet",
            ):
                store.upsert_category_mapping(
                    channel,
                    canonical_category,
                    status=CategoryMappingStatus.CATEGORY_DISCOVERY_BLOCKED_BY_CREDENTIALS,
                )
        _operational_runtime_instance = OperationalRuntime(
            str(settings.runtime_dir / "runtime.sqlite3"),
            product_source=discover_products,
            marketplace_store=store,
            connection_service=MarketplaceConnectionService(store),
        )
    return _operational_runtime_instance


@app.route("/os/agents", methods=["GET"])
def os_agents():
    denied = _admin_required()
    if denied:
        return denied
    from veratus_agents.operations import AGENT_REGISTRY

    return jsonify(
        {
            "agents": [
                _json_safe({"id": key, **value.__dict__})
                for key, value in AGENT_REGISTRY.items()
            ]
        }
    ), 200


@app.route("/os/agents/<agent_id>", methods=["GET"])
def os_agent(agent_id):
    denied = _admin_required()
    if denied:
        return denied
    from veratus_agents.operations import AGENT_REGISTRY

    definition = AGENT_REGISTRY.get(agent_id)
    if definition is None:
        return jsonify({"status": "error", "message": "agent_not_found"}), 404
    runtime = _runtime().tasks.runtimes[agent_id]
    return jsonify(
        {
            "definition": _json_safe({"id": agent_id, **definition.__dict__}),
            "runtime": _json_safe(runtime.__dict__),
        }
    ), 200


@app.route("/os/tasks", methods=["GET"])
def os_tasks():
    denied = _admin_required()
    if denied:
        return denied
    return jsonify(
        {"tasks": [task.__dict__ for task in _runtime().tasks.tasks.values()]}
    ), 200


@app.route("/os/tasks/<task_id>", methods=["GET"])
def os_task(task_id):
    denied = _admin_required()
    if denied:
        return denied
    task = _runtime().tasks.tasks.get(task_id)
    if task is None:
        return jsonify({"status": "error", "message": "task_not_found"}), 404
    return jsonify({"task": task.__dict__}), 200


@app.route("/os/commands", methods=["GET", "POST"])
def os_commands():
    denied = _admin_required()
    if denied:
        return denied
    engine = _runtime()
    if request.method == "GET":
        return jsonify(
            {"commands": [command.__dict__ for command in engine.commands.values()]}
        ), 200
    data, error = _agent_json()
    if error:
        return jsonify({"status": "error", "message": error[0]}), error[1]
    if set(data) != {"message"} or not isinstance(data["message"], str):
        return jsonify({"status": "error", "message": "invalid_command_request"}), 400
    try:
        execution = _operational_runtime().execute(data["message"])
    except ValueError:
        return jsonify({"status": "error", "message": "unsupported_command"}), 400
    command = engine.commands[execution["command_id"]]
    return jsonify(
        {
            "status": "executed",
            "command": _json_safe(command.__dict__),
            "execution": _json_safe(execution),
        }
    ), 201


@app.route("/os/approvals", methods=["GET"])
def os_approvals():
    denied = _admin_required()
    if denied:
        return denied
    return jsonify(
        {
            "approvals": [
                approval.__dict__ for approval in _runtime().approvals.requests.values()
            ]
        }
    ), 200


def _resolve_approval(request_id, status):
    denied = _admin_required()
    if denied:
        return denied
    try:
        approval = _runtime().approvals.resolve(request_id, status)
    except (KeyError, ValueError):
        return jsonify({"status": "error", "message": "approval_not_resolvable"}), 409
    _runtime().tasks.record("founder", "APPROVAL_RESOLVED", request_id, status.value)
    return jsonify({"approval": approval.__dict__}), 200


@app.route("/os/approvals/<request_id>/approve", methods=["POST"])
def os_approve(request_id):
    from veratus_agents.operations import ApprovalStatus

    return _resolve_approval(request_id, ApprovalStatus.APPROVED)


@app.route("/os/approvals/<request_id>/reject", methods=["POST"])
def os_reject(request_id):
    from veratus_agents.operations import ApprovalStatus

    return _resolve_approval(request_id, ApprovalStatus.REJECTED)


@app.route("/os/audit", methods=["GET"])
def os_audit():
    denied = _admin_required()
    if denied:
        return denied
    return jsonify({"audit": [event.__dict__ for event in _runtime().tasks.audit]}), 200


@app.route("/os/status", methods=["GET"])
def os_status():
    denied = _admin_required()
    if denied:
        return denied
    engine = _runtime()
    from veratus_agents.data_discovery import discovery_report

    discovery = discovery_report()
    store = _marketplace_store()
    listings = store.listings()
    return jsonify(
        {
            "agents": engine.tasks.status(),
            "channels": store.channels(),
            "products": {
                "total": discovery["products_total"],
                "complete": discovery["products_complete"],
                "incomplete": discovery["products_incomplete"],
                "segments": discovery["segments"],
            },
            "task_count": len(engine.tasks.tasks),
            "command_count": len(engine.commands),
            "drafts": sum(item["state"] == "DRAFT" for item in listings),
            "approvals": len(store.approvals()) + len(engine.approvals.requests),
            "errors": sum(bool(item.get("last_error")) for item in listings),
            "sync_conflicts": len(store.sync_conflicts()),
            "external_write": False,
            "marketplace_mode": os.getenv("MARKETPLACE_MODE", "LOCAL"),
        }
    ), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
