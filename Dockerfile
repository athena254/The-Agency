FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml uv.lock README.md ./
COPY src/agency/ ./src/agency/

# Install uv and project dependencies
RUN curl -LsSf https://astral.sh/uv/install.sh | sh && \
    export PATH="$HOME/.cargo/bin:$PATH" && \
    uv sync --no-dev --frozen

# Copy config
COPY config/ ./config/

# Set environment
ENV PYTHONPATH=/app/src
ENV AGENCY_CONFIG_PATH=/app/config

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD curl -f http://localhost:8080/health || exit 1

EXPOSE 8080

CMD ["uv", "run", "python", "-m", "agency.api.server", "--host", "0.0.0.0", "--port", "8080"]
