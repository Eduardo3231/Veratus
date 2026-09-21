FROM python:3.11-slim
WORKDIR /app
COPY integrations/requirements.txt /app/integrations/requirements.txt
RUN python -m pip install --no-cache-dir -r integrations/requirements.txt
COPY integrations /app/integrations
COPY veratus_agents /app/veratus_agents
COPY catalog /app/catalog
COPY landing /app/landing
RUN addgroup --system veratus && adduser --system --ingroup veratus veratus \
    && mkdir -p /app/runtime \
    && chown -R veratus:veratus /app
USER veratus
ENV FLASK_APP=integrations.webhook:app
EXPOSE 5000
CMD ["sh", "-c", "exec gunicorn -b 0.0.0.0:${PORT:-5000} --workers ${WEB_CONCURRENCY:-2} --threads ${WEB_THREADS:-4} --timeout 120 --graceful-timeout 30 --max-requests 1000 --max-requests-jitter 100 integrations.webhook:app"]
