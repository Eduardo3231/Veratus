"""Veratus lead-capture webhook. Configure it exclusively with environment variables."""
import csv
import datetime as dt
import os
import re
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

import requests
from flask import Flask, jsonify, request, send_from_directory

LANDING_DIR = Path(__file__).resolve().parents[1] / "landing"
app = Flask(__name__, static_folder=str(LANDING_DIR), static_url_path="")
MAX_REQUESTS_PER_WINDOW = int(os.getenv("RATE_LIMIT_REQUESTS", "10"))
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
ALLOWED_ORIGINS = {item.strip().rstrip("/") for item in os.getenv("FRONTEND_ORIGINS", "").split(",") if item.strip()}
MAILERLITE_API_KEY = os.getenv("MAILERLITE_API_KEY")
MAILERLITE_GROUP_ID = os.getenv("MAILERLITE_GROUP_ID")
MAILERLITE_API_VERSION = os.getenv("MAILERLITE_API_VERSION", "")
LEADS_CSV = Path(os.getenv("LEADS_CSV_PATH", str(Path(__file__).resolve().parents[1] / "leads.csv")))
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
request_buckets, bucket_lock = defaultdict(deque), threading.Lock()

def _headers():
    headers = {"Authorization": f"Bearer {MAILERLITE_API_KEY}", "Content-Type": "application/json"}
    if MAILERLITE_API_VERSION: headers["X-Version"] = MAILERLITE_API_VERSION
    return headers

def _origin_allowed():
    origin = request.headers.get("Origin")
    return not origin or not ALLOWED_ORIGINS or origin.rstrip("/") in ALLOWED_ORIGINS

def _rate_limit_ok():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()
    now = time.monotonic()
    with bucket_lock:
        bucket = request_buckets[ip]
        while bucket and bucket[0] <= now - RATE_LIMIT_WINDOW: bucket.popleft()
        if len(bucket) >= MAX_REQUESTS_PER_WINDOW: return False
        bucket.append(now)
    return True

def _save_lead(lead):
    LEADS_CSV.parent.mkdir(parents=True, exist_ok=True)
    new_file = not LEADS_CSV.exists()
    with LEADS_CSV.open("a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=lead.keys())
        if new_file: writer.writeheader()
        writer.writerow(lead)

def _upsert_mailerlite(email, whatsapp, source, utms):
    if not MAILERLITE_API_KEY: return True
    fields = {"source": source, **{key: value for key, value in utms.items() if value}}
    if whatsapp: fields["whatsapp"] = whatsapp
    payload = {"email": email, "fields": fields}
    if MAILERLITE_GROUP_ID: payload["groups"] = [MAILERLITE_GROUP_ID]
    try:
        response = requests.post("https://connect.mailerlite.com/api/subscribers", json=payload, headers=_headers(), timeout=12)
        if response.ok: return True
        app.logger.warning("MailerLite returned %s", response.status_code)
    except requests.RequestException:
        app.logger.exception("MailerLite request failed")
    return False

@app.after_request
def security_headers(response):
    origin = request.headers.get("Origin")
    if origin and origin.rstrip("/") in ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response

@app.route("/health", methods=["GET"])
def health(): return jsonify(status="ok"), 200

@app.route("/", methods=["GET"])
def landing(): return send_from_directory(LANDING_DIR, "index.html")

@app.route("/webhook", methods=["OPTIONS"])
def webhook_options():
    response = app.make_response(("", 204))
    response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response

@app.route("/webhook", methods=["POST"])
def webhook():
    if not _origin_allowed(): return jsonify(error="Origem não autorizada."), 403
    if not _rate_limit_ok(): return jsonify(error="Muitas tentativas. Aguarde um minuto e tente novamente."), 429
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    if data.get("company"): return jsonify(status="ok"), 200  # Honeypot: do not store bot data.
    email = str(data.get("email", "")).strip().lower()
    whatsapp = str(data.get("whatsapp", "")).strip()[:32]
    consent = str(data.get("consent", "")).lower() in {"true", "1", "on", "yes"}
    if not EMAIL_PATTERN.fullmatch(email): return jsonify(error="Informe um e-mail válido."), 422
    if not consent: return jsonify(error="Confirme o consentimento para continuar."), 422
    source = str(data.get("source", "landing")).strip()[:80] or "landing"
    utms = {key: str(data.get(key, "")).strip()[:120] for key in ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")}
    lead = {"timestamp": dt.datetime.now(dt.timezone.utc).isoformat(), "email": email, "whatsapp": whatsapp, "source": source, "consent": "true", **utms}
    _save_lead(lead)
    if not _upsert_mailerlite(email, whatsapp, source, utms):
        return jsonify(error="Não foi possível concluir o cadastro. Tente novamente em instantes."), 503
    return jsonify(status="ok"), 201

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), debug=os.getenv("FLASK_DEBUG") == "1")
