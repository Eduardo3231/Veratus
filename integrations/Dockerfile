FROM python:3.11-slim
WORKDIR /app
COPY integrations/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home appuser
COPY integrations/webhook.py integrations/push_to_mailerlite.py ./
COPY landing ./landing
USER appuser
ENV PORT=5000 PYTHONUNBUFFERED=1
EXPOSE 5000
CMD ["sh", "-c", "gunicorn --workers 2 --threads 4 --bind 0.0.0.0:${PORT} webhook:app"]
