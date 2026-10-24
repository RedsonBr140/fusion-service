# Backend (Flask API). The frontend is built and served by its own image
# (frontend/Dockerfile) — this image never serves frontend/dist in this
# split-container k8s setup, even though app.py still can for local/dev use.
FROM python:3.12-slim AS base

# pyodbc's wheel needs libodbc.so.2 at import time (see requirements.txt) —
# a plain slim image doesn't have it. unixodbc gives us that shared lib.
# It does NOT include an IBM DB2 ODBC/CLI driver: CISS_DB_DRIVER must name
# a driver registered in odbcinst.ini that is supplied separately (e.g. a
# mounted volume with IBM's clidriver, in this repo's case) — that driver
# is proprietary and isn't something pip or apt can install here.
#
# libxml2 isn't for us — it's the one dependency libdb2o.so (clidriver)
# itself needs via dlopen and doesn't bundle, confirmed via `ldd` against
# a real clidriver build. Without it, the driver loads with a generic
# "file not found" from unixODBC that doesn't name the real missing lib.
RUN apt-get update \
    && apt-get install -y --no-install-recommends unixodbc curl libxml2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY app.py config.py database.py schema.sql ./
COPY auth ./auth
COPY cargas_fusion ./cargas_fusion
COPY integrations ./integrations

RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

ENV DATABASE_PATH=/app/data/fusion.db \
    PORT=5050 \
    PYTHONUNBUFFERED=1

EXPOSE 5050

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/api/health" || exit 1

CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT} --workers 2 --threads 4 --timeout 60 'app:create_app()'"]
