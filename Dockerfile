# The Agency — API image.
#
# Runs the FastAPI/uvicorn application defined by src/agency/api/server.py.
# The only public route is GET /v1/health; every other /v1/* business route is
# closed with HTTP 503 until peer authority exists (see src/agency/api/server.py).
#
# Storage is SQLite via aiosqlite (evidence/memory) plus the Lattice SQLite
# store. Redis and PostgreSQL are NOT used by the runtime and are absent.
#
# uv is pinned so the image resolves exactly what uv.lock records.

FROM python:3.15-slim

ARG UV_VERSION=0.13.1

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_PYTHON_PREFERENCE=only-system

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir "uv==${UV_VERSION}"

COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/

# Install the runtime dependencies and the project from the frozen lock.
RUN uv sync --no-dev --frozen

COPY config/ ./config/

# Persistent runtime data lives on a mounted volume, never in the image layer.
RUN mkdir -p /app/data \
    && groupadd --system --gid 10001 agency \
    && useradd --system --uid 10001 --gid agency --home /app --shell /usr/sbin/nologin agency \
    && chown -R agency:agency /app/data

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONPATH=/app/src \
    AGENCY_EVIDENCE_DB=/app/data/evidence.db \
    AGENCY_MEMORY_DB=/app/data/memory.db \
    LATTICE_SQLITE_PATH=/app/data/lattice.db

USER agency
EXPOSE 8000

# The health probe must match the application's real public endpoint.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/v1/health || exit 1

# agency.api.server is an ASGI app, not a module runner: serve it with uvicorn.
CMD ["/app/.venv/bin/uvicorn", "agency.api.server:app", "--host", "0.0.0.0", "--port", "8000"]
