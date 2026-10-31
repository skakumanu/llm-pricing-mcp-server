# v1.86.0: Litestream binary for continuous SQLite replication (see litestream.yml and
# docs/DEPLOYMENT.md). Copied out of the official image so nothing is downloaded at build time.
FROM litestream/litestream:0.3.13 AS litestream

# Use Python 3.11 slim image
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Litestream binary + config + the entrypoint that supervises uvicorn (v1.86.0).
# entrypoint.sh sits at the repo root because scripts/ is not copied into the image.
COPY --from=litestream /usr/local/bin/litestream /usr/local/bin/litestream
COPY litestream.yml /etc/litestream.yml
COPY entrypoint.sh ./entrypoint.sh
RUN chmod 755 /app/entrypoint.sh

# Copy application code
COPY src/ ./src/
COPY static/ ./static/
COPY agent/ ./agent/
COPY rag/ ./rag/
COPY mcp/ ./mcp/
COPY docs/ ./docs/
# Vendored data (price-registry snapshot, exchange-rate table). NOTE: fly.toml
# mounts the persistent volume at /app/data, which shadows this directory at
# runtime on Fly — so the exchange-rate table is also copied outside the mount
# and EXCHANGE_RATES_PATH in fly.toml points there (v1.76.0).
COPY data/ ./data/
COPY data/exchange_rates.json ./vendored/exchange_rates.json
# v1.84.1: the same shadowing hid the registry snapshot and every registry-sourced model
# file from production, so the whole data/ tree is also kept outside the mount and
# PRICE_SNAPSHOT_PATH / REGISTRY_MODELS_DIR in fly.toml point there.
COPY data/ ./vendored/data/
COPY Procfile .
COPY runtime.txt .

# Create non-root user
RUN useradd -m appuser && chown -R appuser:appuser /app

USER appuser

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run the application
# entrypoint.sh replicates the databases with Litestream when LITESTREAM_REPLICA_URL is set,
# and otherwise runs plain uvicorn (--workers 2) exactly as before.
CMD ["/app/entrypoint.sh"]
