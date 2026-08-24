"""One-off sync of locally captured Veratus leads to the current MailerLite API."""
import csv
import os
from pathlib import Path
import requests

API_KEY = os.getenv("MAILERLITE_API_KEY")
GROUP_ID = os.getenv("MAILERLITE_GROUP_ID")
LEADS_CSV = Path(os.getenv("LEADS_CSV_PATH", str(Path(__file__).resolve().parents[1] / "leads.csv")))

def main():
    if not API_KEY: raise SystemExit("Defina MAILERLITE_API_KEY antes de executar.")
    if not LEADS_CSV.exists(): raise SystemExit(f"Arquivo não encontrado: {LEADS_CSV}")
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    with LEADS_CSV.open(newline="", encoding="utf-8") as file: leads = list(csv.DictReader(file))
    for index, lead in enumerate(leads, 1):
        if not lead.get("email") or lead.get("consent") != "true": continue
        fields = {key: lead[key] for key in ("whatsapp", "source", "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term") if lead.get(key)}
        payload = {"email": lead["email"], "fields": fields}
        if GROUP_ID: payload["groups"] = [GROUP_ID]
        response = requests.post("https://connect.mailerlite.com/api/subscribers", json=payload, headers=headers, timeout=15)
        print(f"[{index}/{len(leads)}] {lead['email']}: {'OK' if response.ok else f'ERRO {response.status_code}'}")

if __name__ == "__main__": main()
