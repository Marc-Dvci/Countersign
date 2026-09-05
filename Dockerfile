# The Countersign console: API, deterministic control tests, SQLite, static UI.
#
# The container is the sole writer of the database and mounts /app/data on
# durable storage. It runs unprivileged and installs from a hash-pinned lock.

FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1
WORKDIR /build
COPY requirements.lock ./
RUN python -m pip install setuptools==80.9.0 \
    && python -m pip install --require-hashes -r requirements.lock
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip wheel --no-deps --no-build-isolation . --wheel-dir /wheels

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    COUNTERSIGN_DATABASE_PATH=/app/data/countersign.db \
    COUNTERSIGN_SESSION_PATH=/app/data/strands-sessions \
    COUNTERSIGN_HOST=0.0.0.0 \
    COUNTERSIGN_PORT=8080
WORKDIR /app

RUN addgroup --system countersign \
    && adduser --system --ingroup countersign --home /app countersign

COPY requirements.lock ./
COPY --from=builder /wheels /wheels
RUN python -m pip install --require-hashes -r requirements.lock \
    && python -m pip install --no-deps /wheels/countersign-1.0.0-py3-none-any.whl \
    && rm -rf /wheels requirements.lock

RUN mkdir -p /app/data && chown -R countersign:countersign /app
USER countersign
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/state', timeout=2)"

CMD ["python", "-m", "countersign.cli", "serve"]
